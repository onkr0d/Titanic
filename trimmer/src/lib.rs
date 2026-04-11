pub mod error;
pub mod trim;

use axum::{
    Router,
    extract::{Query, State},
    http::StatusCode,
    response::{Html, IntoResponse, Json, Response},
    routing::{get, post},
};
use tower_http::services::ServeDir;
use tower_http::trace::TraceLayer;

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tokio::sync::Semaphore;
use tracing::{info, warn};

use error::AppError;

/// Max concurrent thumbnail generations to keep CPU load low.
const THUMB_CONCURRENCY: usize = 2;

/// Background task: pre-generate thumbnails for all videos in Clips/.
/// Uses a semaphore to limit to THUMB_CONCURRENCY at a time.
pub async fn pre_generate_thumbnails(state: Arc<AppState>) {
    let clips_dir = state.media_path.join("Clips");
    if !clips_dir.exists() {
        info!("No Clips/ directory found, skipping thumbnail pre-generation");
        return;
    }

    let thumb_dir = state.data_dir.join("thumbs");

    // Collect all video paths
    let mut video_paths = Vec::new();
    if let Err(e) = collect_video_paths(&clips_dir, &mut video_paths) {
        warn!("Failed to scan for videos: {e}");
        return;
    }

    if video_paths.is_empty() {
        info!("No videos found for thumbnail pre-generation");
        return;
    }

    info!(
        "Pre-generating thumbnails for {} videos (concurrency: {})",
        video_paths.len(),
        THUMB_CONCURRENCY
    );

    let semaphore = Arc::new(Semaphore::new(THUMB_CONCURRENCY));
    let mut handles = Vec::new();

    for video_path in video_paths {
        let sem = semaphore.clone();
        let cache_dir = thumb_dir.clone();

        handles.push(tokio::spawn(async move {
            let _permit = sem.acquire().await.unwrap();
            match trim::generate_thumbnail(&video_path, &cache_dir).await {
                Ok(_) => info!("Thumbnail ready: {:?}", video_path.file_name().unwrap_or_default()),
                Err(e) => warn!("Thumbnail failed for {:?}: {e}", video_path.file_name().unwrap_or_default()),
            }
        }));
    }

    // Wait for all to finish
    for handle in handles {
        let _ = handle.await;
    }

    info!("Thumbnail pre-generation complete");
}

fn collect_video_paths(dir: &Path, paths: &mut Vec<PathBuf>) -> Result<(), String> {
    let entries = std::fs::read_dir(dir).map_err(|e| e.to_string())?;
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_video_paths(&path, paths)?;
        } else if is_video_file(&path) {
            paths.push(path);
        }
    }
    Ok(())
}

const VALID_VIDEO_EXTENSIONS: &[&str] = &[
    "mp4", "avi", "mov", "mkv", "wmv", "flv", "m4v", "webm", "ts",
];

pub struct AppState {
    pub media_path: PathBuf,
    pub data_dir: PathBuf,
}

#[derive(Debug, Serialize)]
pub struct HealthResponse {
    status: String,
    timestamp: chrono::DateTime<chrono::Utc>,
}

#[derive(Debug, Serialize)]
pub struct VideoEntry {
    path: String,
    name: String,
    folder: String,
    size: u64,
    modified: i64,
}

#[derive(Debug, Serialize)]
pub struct VideosResponse {
    videos: Vec<VideoEntry>,
}

#[derive(Debug, Deserialize)]
pub struct PathQuery {
    path: String,
}

pub fn build_router(state: Arc<AppState>) -> Router<()> {
    Router::new()
        .route("/health", get(health_check))
        .route("/api/videos", get(list_videos))
        .route("/api/video", get(serve_video))
        .route("/api/thumbnail", get(serve_thumbnail))
        .route("/api/trim", post(handle_trim))
        .route("/api/duration", get(get_duration))
        .nest_service("/static", ServeDir::new("static"))
        .route("/", get(index_page))
        .layer(TraceLayer::new_for_http())
        .with_state(state)
}

async fn health_check() -> Json<HealthResponse> {
    Json(HealthResponse {
        status: "healthy".to_string(),
        timestamp: chrono::Utc::now(),
    })
}

async fn index_page() -> Html<&'static str> {
    Html(include_str!("../static/index.html"))
}

async fn list_videos(
    State(state): State<Arc<AppState>>,
) -> Result<Json<VideosResponse>, AppError> {
    let clips_dir = state.media_path.join("Clips");
    if !clips_dir.exists() {
        return Ok(Json(VideosResponse { videos: vec![] }));
    }

    let mut videos = Vec::new();
    collect_videos(&clips_dir, &state.media_path, &mut videos)?;

    // Sort by modification time (newest first)
    videos.sort_by(|a, b| b.modified.cmp(&a.modified));

    Ok(Json(VideosResponse { videos }))
}

fn collect_videos(
    dir: &Path,
    media_root: &Path,
    videos: &mut Vec<VideoEntry>,
) -> Result<(), AppError> {
    let entries = std::fs::read_dir(dir)
        .map_err(|e| AppError::InternalError(format!("Failed to read directory: {e}")))?;

    for entry in entries {
        let entry = entry
            .map_err(|e| AppError::InternalError(format!("Failed to read entry: {e}")))?;
        let path = entry.path();

        if path.is_dir() {
            collect_videos(&path, media_root, videos)?;
        } else if is_video_file(&path) {
            let metadata = std::fs::metadata(&path)
                .map_err(|e| AppError::InternalError(format!("Failed to stat file: {e}")))?;

            let relative = path
                .strip_prefix(media_root)
                .unwrap_or(&path)
                .to_string_lossy()
                .to_string();

            let folder = path
                .parent()
                .and_then(|p| p.strip_prefix(media_root.join("Clips")).ok())
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_default();

            let name = path
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default();

            let modified = metadata
                .modified()
                .unwrap_or(std::time::SystemTime::UNIX_EPOCH)
                .duration_since(std::time::SystemTime::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs() as i64;

            videos.push(VideoEntry {
                path: relative,
                name,
                folder,
                size: metadata.len(),
                modified,
            });
        }
    }

    Ok(())
}

fn is_video_file(path: &Path) -> bool {
    path.extension()
        .and_then(|e| e.to_str())
        .is_some_and(|ext| VALID_VIDEO_EXTENSIONS.contains(&ext.to_lowercase().as_str()))
}

async fn serve_video(
    State(state): State<Arc<AppState>>,
    Query(params): Query<PathQuery>,
) -> Result<Response, AppError> {
    let video_path = trim::ensure_path_within(&state.media_path, Path::new(&params.path))?;

    if !video_path.exists() {
        return Err(AppError::NotFound("Video not found".into()));
    }

    // Determine content type from extension
    let content_type = match video_path.extension().and_then(|e| e.to_str()) {
        Some("mp4") | Some("m4v") => "video/mp4",
        Some("webm") => "video/webm",
        Some("mkv") => "video/x-matroska",
        Some("avi") => "video/x-msvideo",
        Some("mov") => "video/quicktime",
        Some("wmv") => "video/x-ms-wmv",
        Some("flv") => "video/x-flv",
        Some("ts") => "video/mp2t",
        _ => "application/octet-stream",
    };

    let file_data = tokio::fs::read(&video_path)
        .await
        .map_err(|e| AppError::InternalError(format!("Failed to read video: {e}")))?;

    Ok((
        StatusCode::OK,
        [
            ("Content-Type", content_type),
            ("Accept-Ranges", "bytes"),
        ],
        file_data,
    )
        .into_response())
}

async fn serve_thumbnail(
    State(state): State<Arc<AppState>>,
    Query(params): Query<PathQuery>,
) -> Result<Response, AppError> {
    let video_path = trim::ensure_path_within(&state.media_path, Path::new(&params.path))?;

    if !video_path.exists() {
        return Err(AppError::NotFound("Video not found".into()));
    }

    let thumb_dir = state.data_dir.join("thumbs");
    let thumb_path = trim::generate_thumbnail(&video_path, &thumb_dir).await?;

    let thumb_data = tokio::fs::read(&thumb_path)
        .await
        .map_err(|e| AppError::InternalError(format!("Failed to read thumbnail: {e}")))?;

    Ok((
        StatusCode::OK,
        [("Content-Type", "image/avif")],
        thumb_data,
    )
        .into_response())
}

#[derive(Debug, Serialize)]
struct DurationResponse {
    duration: f64,
}

async fn get_duration(
    State(state): State<Arc<AppState>>,
    Query(params): Query<PathQuery>,
) -> Result<Json<DurationResponse>, AppError> {
    let video_path = trim::ensure_path_within(&state.media_path, Path::new(&params.path))?;

    if !video_path.exists() {
        return Err(AppError::NotFound("Video not found".into()));
    }

    let duration = trim::get_video_duration(&video_path).await?;
    Ok(Json(DurationResponse { duration }))
}

async fn handle_trim(
    State(state): State<Arc<AppState>>,
    Json(req): Json<trim::TrimRequest>,
) -> Result<Json<trim::TrimResponse>, AppError> {
    info!(
        "Trim request: path={}, start={}, end={}, overwrite={}",
        req.path, req.start_time, req.end_time, req.overwrite
    );

    let result = trim::trim_video(&state.media_path, &req).await?;
    Ok(Json(result))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn video_extensions_detected() {
        for ext in VALID_VIDEO_EXTENSIONS {
            let path = PathBuf::from(format!("test.{ext}"));
            assert!(is_video_file(&path), "{ext} should be valid");
        }
    }

    #[test]
    fn non_video_rejected() {
        assert!(!is_video_file(Path::new("image.jpg")));
        assert!(!is_video_file(Path::new("doc.pdf")));
        assert!(!is_video_file(Path::new("noext")));
    }
}
