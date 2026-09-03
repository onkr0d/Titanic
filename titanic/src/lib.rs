pub mod auth;
pub mod config;
pub mod error;
pub mod settings;
pub mod upload;

use axum::{
    Router,
    extract::{DefaultBodyLimit, Multipart, State},
    http::HeaderMap,
    response::Json,
    routing::{get, post},
};
use axum::extract::multipart::MultipartError;
use axum::http::{HeaderName, HeaderValue, Method};
use tower_http::{cors::CorsLayer, limit::RequestBodyLimitLayer, trace::TraceLayer};

use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tokio::fs::File;
use tokio::io::AsyncWriteExt;
use tracing::info;

use error::AppError;
use upload::SpaceInfo;

#[derive(Debug, Serialize, Deserialize)]
struct HealthResponse {
    status: String,
    timestamp: chrono::DateTime<chrono::Utc>,
}

#[derive(Debug, Serialize, Deserialize)]
struct UploadResponse {
    message: String,
    filename: String,
    plex_path: String,
    folder: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct FoldersResponse {
    folders: Vec<String>,
}

pub struct AppState {
    pub auth: auth::FirebaseAuth,
    pub uploader: upload::VideoUploader,
    pub data_dir: String,
    pub sentry_guard: settings::SentryGuard,
}

impl From<MultipartError> for AppError {
    fn from(err: MultipartError) -> Self {
        AppError::UploadError(err.body_text())
    }
}

const CONTENT_LENGTH_LIMIT: usize = 20 * 1024 * 1024 * 1024; // 20GB

/// Body limit for the settings listener. It only ever receives a small JSON
/// document, so it has no business accepting the 20GB the upload port does.
const SETTINGS_BODY_LIMIT: usize = 64 * 1024; // 64KB

/// Build the tailnet-facing router served on the published port (3029).
///
/// Anything that can reach the published port can reach these routes — in
/// production that is the VPS over Tailscale, and the whole LAN if the tailnet
/// bind ever falls back. So every route here verifies a Firebase token, and the
/// settings page plus its read/write API are deliberately absent: they live on
/// the unpublished listener built by `build_private_router`.
///
/// `/api/settings` exists here only as a redacted, read-only projection
/// (`default_folder` and nothing else) because the VPS's `/api/config` depends
/// on it. The Sentry DSN never crosses the tailnet.
pub fn build_public_router(state: Arc<AppState>) -> Router<()> {
    let cors = CorsLayer::new()
        .allow_origin([
            HeaderValue::from_static("https://titanic.ivan.boston"),
            HeaderValue::from_static("http://localhost:5173"),
            HeaderValue::from_static("http://localhost:6969"),
            HeaderValue::from_static("http://localhost:5002"),
        ])
        .allow_methods([
            Method::GET,
            Method::POST,
            Method::PUT,
            Method::OPTIONS,
        ])
        .allow_headers([
            HeaderName::from_static("content-type"),
            HeaderName::from_static("authorization"),
            HeaderName::from_static("x-firebase-appcheck"),
            HeaderName::from_static("baggage"),
            HeaderName::from_static("sentry-trace"),
        ]);

    Router::new()
        .route("/health", get(health_check))
        .route("/api/upload", post(upload_video))
        .route("/api/space", get(space_check))
        .route("/api/folders", get(list_folders))
        .route("/api/settings", get(settings::get_public_settings))
        .layer(TraceLayer::new_for_http())
        .layer(cors)
        .layer(DefaultBodyLimit::disable())
        .layer(RequestBodyLimitLayer::new(CONTENT_LENGTH_LIMIT))
        .with_state(state)
}

/// Build the settings router served on the unpublished port (3031).
///
/// `docker-compose.yml` publishes no host mapping for this port, so the only
/// route to it is Umbrel's app_proxy over the app network — which is already
/// behind the Umbrel login. That proxy *is* the authentication for these
/// routes, which is why they carry no token check of their own: the settings
/// page is plain HTML with no Firebase SDK and no way to mint a token.
///
/// The security property this router relies on is therefore a deployment one:
/// **this port must never appear in a `ports:` mapping.** The route-level half
/// of that property is pinned by `settings_page_is_absent_from_public_router`
/// and `public_router_refuses_settings_writes` in tests/integration.rs.
pub fn build_private_router(state: Arc<AppState>) -> Router<()> {
    Router::new()
        // Also served here so Umbrel's app_proxy `initialCheck` (which targets
        // this port) has something to poll.
        .route("/health", get(health_check))
        .route("/", get(settings::settings_page))
        .route("/settings", get(settings::settings_page))
        .route("/api/settings", get(settings::get_settings).put(settings::put_settings))
        // The settings page populates its folder dropdown from this; same data as
        // the public route, minus the token check the page cannot satisfy.
        .route("/api/folders", get(list_folders_local))
        .layer(TraceLayer::new_for_http())
        .layer(RequestBodyLimitLayer::new(SETTINGS_BODY_LIMIT))
        .with_state(state)
}

async fn health_check() -> Json<HealthResponse> {
    Json(HealthResponse {
        status: "healthy".to_string(),
        timestamp: chrono::Utc::now(),
    })
}

async fn upload_video(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    mut multipart: Multipart,
) -> Result<Json<UploadResponse>, AppError> {
    info!("Received an upload request");
    // Log headers for debugging, redacting sensitive values
    const SENSITIVE_HEADERS: &[&str] = &["authorization", "cookie", "x-firebase-appcheck"];
    for (key, value) in headers.iter() {
        if SENSITIVE_HEADERS.contains(&key.as_str()) {
            info!("Header: {} = [REDACTED]", key.as_str());
        } else {
            info!("Header: {} = {:?}", key.as_str(), value);
        }
    }

    // Verify Firebase authentication
    let user = state.auth.verify_token(&headers).await?;
    info!("Upload request from user: {}", user.email);

    // Create a temporary file to stream the upload
    let temp_dir = std::env::temp_dir();
    let temp_file_path = temp_dir.join(format!(
        "upload_{}_{}",
        chrono::Utc::now().timestamp_nanos_opt().unwrap_or(0),
        "tempfile"
    ));
    let mut temp_file = File::create(&temp_file_path)
        .await
        .map_err(|e| AppError::InternalError(format!("Failed to create temp file: {e}")))?;

    // Extract file and folder from multipart
    let mut filename: Option<String> = None;
    let mut folder: Option<String> = None;
    let mut field_found = false;

    info!("Starting multipart processing");

    while let Some(field) = multipart.next_field().await? {
        match field.name() {
            Some("file") => {
                filename = field.file_name().map(|f| f.to_owned());
                field_found = true;

                let mut field_stream = field;
                while let Some(chunk) = field_stream.chunk().await? {
                    temp_file.write_all(&chunk).await.map_err(|e| {
                        AppError::InternalError(format!("Failed to write to temp file: {e}"))
                    })?;
                }
                // Don't break - continue processing other fields
            }
            Some("folder") => {
                if let Ok(text) = field.text().await {
                    folder = if text.trim().is_empty() {
                        None
                    } else {
                        Some(text.trim().to_string())
                    };
                    info!("Received folder parameter: {:?}", folder);
                }
            }
            Some(other) => {
                info!("Received other field: {}", other);
            }
            _ => {} // Ignore other fields
        }
    }

    // Ensure the temp file is closed
    drop(temp_file);

    if !field_found {
        // Clean up temp file if it was created but no field was found
        let _ = tokio::fs::remove_file(&temp_file_path).await;
        return Err(AppError::UploadError(
            "No 'file' field in multipart request".to_string(),
        ));
    }

    let filename =
        filename.ok_or_else(|| AppError::UploadError("No filename provided".to_string()))?;

    // Validate file extension
    if !is_valid_video_file(&filename) {
        // Clean up the temp file before returning the error
        let _ = tokio::fs::remove_file(&temp_file_path).await;
        return Err(AppError::UploadError("Invalid file type".to_string()));
    }

    // Upload to Plex media directory by moving the temp file
    info!(
        "About to save video: filename={}, folder={:?}",
        filename, folder
    );
    let plex_path = state
        .uploader
        .upload_video(&filename, &temp_file_path, folder.as_deref())
        .await?;
    info!("Upload completed, saved to: {}", plex_path);

    // The temp file is moved by upload_video, so no need to delete it here.

    info!("Successfully saved {} to {}", filename, plex_path);

    Ok(Json(UploadResponse {
        message: "File saved successfully".to_string(),
        filename,
        plex_path,
        folder: folder.clone(),
    }))
}

async fn space_check(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> Result<Json<SpaceInfo>, AppError> {
    // Verify Firebase authentication
    let _user = state.auth.verify_token(&headers).await?;

    let space_info = state.uploader.get_space_info().await?;

    Ok(Json(space_info))
}

/// `GET /api/folders` on the public listener. The VPS forwards the caller's
/// Authorization header for this request, so it can verify like any other route.
async fn list_folders(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> Result<Json<FoldersResponse>, AppError> {
    state.auth.verify_token(&headers).await?;

    folders_response(&state).await
}

/// `GET /api/folders` on the private listener — no token check, because the
/// settings page has no way to produce one and app_proxy has already
/// authenticated the caller. Only ever mounted by `build_private_router`.
async fn list_folders_local(
    State(state): State<Arc<AppState>>,
) -> Result<Json<FoldersResponse>, AppError> {
    folders_response(&state).await
}

async fn folders_response(state: &AppState) -> Result<Json<FoldersResponse>, AppError> {
    let folders = state.uploader.list_folders().await?;

    Ok(Json(FoldersResponse { folders }))
}

pub(crate) fn is_valid_video_file(filename: &str) -> bool {
    let valid_extensions = [
        "mp4", "avi", "mov", "mkv", "wmv", "flv", "m4v", "webm", "ts",
    ];

    if let Some(extension) = filename.split('.').next_back() {
        valid_extensions.contains(&extension.to_lowercase().as_str())
    } else {
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid_video_extensions() {
        for ext in ["mp4", "avi", "mov", "mkv", "wmv", "flv", "m4v", "webm", "ts"] {
            assert!(
                is_valid_video_file(&format!("video.{ext}")),
                "{ext} should be valid"
            );
        }
    }

    #[test]
    fn invalid_extensions_rejected() {
        assert!(!is_valid_video_file("image.jpg"));
        assert!(!is_valid_video_file("doc.pdf"));
        assert!(!is_valid_video_file("script.exe"));
        assert!(!is_valid_video_file("archive.zip"));
    }

    #[test]
    fn no_extension_rejected() {
        assert!(!is_valid_video_file("videofile"));
    }

    #[test]
    fn case_insensitive() {
        assert!(is_valid_video_file("video.MP4"));
        assert!(is_valid_video_file("video.MkV"));
        assert!(is_valid_video_file("video.AVI"));
    }
}
