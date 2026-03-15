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
use tower_http::{cors::CorsLayer, limit::RequestBodyLimitLayer};

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

const CONTENT_LENGTH_LIMIT: usize = 10 * 1024 * 1024 * 1024; // 10GB

/// Build the axum router with all routes and middleware.
/// Extracted from `main()` so integration tests can use it.
/// State is consumed via `.with_state()`, so the returned router is `Router<()>`.
pub fn build_router(state: Arc<AppState>) -> Router<()> {
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
            Method::DELETE,
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
        .route("/", get(settings::settings_page))
        .route("/settings", get(settings::settings_page))
        .route("/api/settings", get(settings::get_settings).put(settings::put_settings))
        .layer(cors)
        .layer(DefaultBodyLimit::disable())
        .layer(RequestBodyLimitLayer::new(CONTENT_LENGTH_LIMIT))
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

async fn list_folders(
    State(state): State<Arc<AppState>>,
) -> Result<Json<FoldersResponse>, AppError> {
    let folders = state.uploader.list_folders().await?;

    Ok(Json(FoldersResponse { folders }))
}

pub(crate) fn is_valid_video_file(filename: &str) -> bool {
    let valid_extensions = [
        "mp4", "avi", "mov", "mkv", "wmv", "flv", "m4v", "avi", "webm", "ts",
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
