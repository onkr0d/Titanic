pub mod error;
pub mod trim;

use axum::{
    Router,
    extract::{Query, State},
    http::{HeaderValue, StatusCode, header},
    response::{IntoResponse, Json, Response},
    routing::{get, post},
};
use tower::{Layer, ServiceExt};
use tower_http::services::{ServeDir, ServeFile};
use tower_http::set_header::SetResponseHeaderLayer;
use tower_http::trace::TraceLayer;

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tokio::sync::{RwLock, Semaphore};
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

/// Background task: probe and cache video durations so sort-by-duration is instant.
pub async fn pre_cache_durations(state: Arc<AppState>) {
    let clips_dir = state.media_path.join("Clips");
    if !clips_dir.exists() {
        return;
    }

    let mut video_paths = Vec::new();
    if let Err(e) = collect_video_paths(&clips_dir, &mut video_paths) {
        warn!("Failed to scan for videos: {e}");
        return;
    }

    if video_paths.is_empty() {
        return;
    }

    info!("Caching durations for {} videos", video_paths.len());

    let semaphore = Arc::new(Semaphore::new(4));

    let futures: Vec<_> = video_paths
        .into_iter()
        .map(|video_path| {
            let state = state.clone();
            let sem = semaphore.clone();
            async move {
                let metadata = match tokio::fs::metadata(&video_path).await {
                    Ok(m) => m,
                    Err(_) => return None,
                };
                let mtime = metadata
                    .modified()
                    .unwrap_or(std::time::SystemTime::UNIX_EPOCH)
                    .duration_since(std::time::SystemTime::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_secs() as i64;

                let relative = video_path
                    .strip_prefix(&state.media_path)
                    .unwrap_or(&video_path)
                    .to_string_lossy()
                    .to_string();

                // Check existing cache — skip if mtime hasn't changed
                {
                    let cache = state.duration_cache.read().await;
                    if let Some(&(cached_mtime, dur)) = cache.get(&relative) {
                        if cached_mtime == mtime {
                            return Some((relative, (mtime, dur)));
                        }
                    }
                }

                let _permit = sem.acquire().await.unwrap();
                match trim::get_video_duration(&video_path).await {
                    Ok(dur) => Some((relative, (mtime, dur))),
                    Err(e) => {
                        warn!("Duration probe failed for {:?}: {e}", video_path);
                        None
                    }
                }
            }
        })
        .collect();

    let results = futures::future::join_all(futures).await;
    let mut new_cache = HashMap::new();
    for result in results.into_iter().flatten() {
        new_cache.insert(result.0, result.1);
    }

    *state.duration_cache.write().await = new_cache;
    info!(
        "Duration cache populated: {} entries",
        state.duration_cache.read().await.len()
    );
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
    /// In-memory cache: relative_path -> (mtime_secs, duration_secs)
    pub duration_cache: RwLock<HashMap<String, (i64, f64)>>,
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
    #[serde(skip_serializing_if = "Option::is_none")]
    duration: Option<f64>,
}

#[derive(Debug, Serialize)]
pub struct VideosResponse {
    videos: Vec<VideoEntry>,
}

#[derive(Debug, Deserialize)]
pub struct PathQuery {
    path: String,
}

#[derive(Debug, Deserialize)]
pub struct VideosQuery {
    #[serde(default)]
    sort: Option<String>,
    #[serde(default)]
    folder: Option<String>,
}

pub fn build_router(state: Arc<AppState>) -> Router<()> {
    // Serve static assets with no-cache + must-revalidate so iOS standalone
    // (Add to Home Screen) mode revalidates and picks up changes instead of serving stale files.
    let static_service = SetResponseHeaderLayer::overriding(
        header::CACHE_CONTROL,
        HeaderValue::from_static("no-cache, must-revalidate"),
    )
    .layer(ServeDir::new("static"));

    Router::new()
        .route("/health", get(health_check))
        .route("/api/videos", get(list_videos))
        .route("/api/video", get(serve_video))
        .route("/api/thumbnail", get(serve_thumbnail))
        .route("/api/trim", post(handle_trim))
        .route("/api/duration", get(get_duration))
        .nest_service("/static", static_service)
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

async fn index_page() -> Response {
    let html = match tokio::fs::read_to_string("static/index.html").await {
        Ok(html) => html,
        Err(_) => {
            // Fallback to compiled-in copy if the file can't be read at runtime
            include_str!("../static/index.html").to_string()
        }
    };

    (
        StatusCode::OK,
        [
            (header::CONTENT_TYPE, "text/html; charset=utf-8"),
            (header::CACHE_CONTROL, "no-cache, must-revalidate"),
        ],
        html,
    )
        .into_response()
}

async fn list_videos(
    State(state): State<Arc<AppState>>,
    Query(params): Query<VideosQuery>,
) -> Result<Json<VideosResponse>, AppError> {
    let clips_dir = state.media_path.join("Clips");
    if !clips_dir.exists() {
        return Ok(Json(VideosResponse { videos: vec![] }));
    }

    let mut videos = Vec::new();
    collect_videos(&clips_dir, &state.media_path, &mut videos)?;

    // Server-side folder filtering
    if let Some(ref folder) = params.folder {
        if !folder.is_empty() {
            videos.retain(|v| v.folder == *folder);
        }
    }

    if params.sort.as_deref() == Some("duration") {
        let cache = state.duration_cache.read().await;
        for video in &mut videos {
            if let Some(&(_mtime, dur)) = cache.get(&video.path) {
                video.duration = Some(dur);
            }
        }
        videos.sort_by(|a, b| {
            b.duration
                .unwrap_or(0.0)
                .partial_cmp(&a.duration.unwrap_or(0.0))
                .unwrap_or(std::cmp::Ordering::Equal)
        });
    } else {
        videos.sort_by(|a, b| b.modified.cmp(&a.modified));
    }

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
                duration: None,
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
    request: axum::extract::Request,
) -> Result<Response, AppError> {
    let video_path = trim::ensure_path_within(&state.media_path, Path::new(&params.path))?;

    if !video_path.exists() {
        return Err(AppError::NotFound("Video not found".into()));
    }

    Ok(ServeFile::new(&video_path)
        .oneshot(request)
        .await
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

    // Try cache first
    {
        let cache = state.duration_cache.read().await;
        if let Some(&(_mtime, dur)) = cache.get(&params.path) {
            return Ok(Json(DurationResponse { duration: dur }));
        }
    }

    // Cache miss — fall back to ffprobe
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

    // Invalidate cache for affected paths
    {
        let mut cache = state.duration_cache.write().await;
        cache.remove(&req.path);
        cache.remove(&result.output_path);
    }

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
