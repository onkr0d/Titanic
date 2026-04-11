use crate::error::AppError;
use path_clean::PathClean;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use tokio::process::Command;
use tracing::info;

/// Verify that `target` resolves to a location within `base`.
pub fn ensure_path_within(base: &Path, target: &Path) -> Result<PathBuf, AppError> {
    let absolute_target = if target.is_absolute() {
        target.to_path_buf().clean()
    } else {
        base.join(target).clean()
    };
    let absolute_base = base.clean();

    if !absolute_target.starts_with(&absolute_base) {
        return Err(AppError::BadRequest(
            "Path escapes base directory".to_string(),
        ));
    }

    Ok(absolute_target)
}

#[derive(Debug, Deserialize)]
pub struct TrimRequest {
    pub path: String,
    #[serde(rename = "startTime")]
    pub start_time: f64,
    #[serde(rename = "endTime")]
    pub end_time: f64,
    #[serde(default)]
    pub overwrite: bool,
}

#[derive(Debug, Serialize)]
pub struct TrimResponse {
    pub message: String,
    pub output_path: String,
}

/// Run FFmpeg to trim a video using stream copy (no re-encoding).
/// If `overwrite` is true, the original file is atomically replaced.
/// Otherwise, a new file is created with a `_trimmed` suffix.
pub async fn trim_video(
    media_path: &Path,
    req: &TrimRequest,
) -> Result<TrimResponse, AppError> {
    // Validate and resolve the source path
    let source = ensure_path_within(media_path, Path::new(&req.path))?;
    if !source.exists() {
        return Err(AppError::NotFound(format!(
            "Video not found: {}",
            req.path
        )));
    }

    // Validate times
    if req.start_time < 0.0 {
        return Err(AppError::BadRequest("Start time cannot be negative".into()));
    }
    if req.end_time <= req.start_time {
        return Err(AppError::BadRequest(
            "End time must be greater than start time".into(),
        ));
    }

    // Determine output path
    let output_path = if req.overwrite {
        // Write to a temp file in the same directory, then rename atomically
        let parent = source.parent().unwrap_or(media_path);
        let stem = source
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("video");
        let ext = source
            .extension()
            .and_then(|e| e.to_str())
            .unwrap_or("mp4");
        parent.join(format!(".{stem}_trimming_tmp.{ext}"))
    } else {
        generate_trimmed_filename(&source)?
    };

    // Validate output path is within media directory
    let output_path = ensure_path_within(media_path, &output_path)?;

    info!(
        "Trimming video: {:?} -> {:?} ({}s to {}s, overwrite={})",
        source, output_path, req.start_time, req.end_time, req.overwrite
    );

    // Build FFmpeg command
    let output = Command::new("ffmpeg")
        .arg("-y") // overwrite output
        .arg("-ss")
        .arg(format!("{:.3}", req.start_time))
        .arg("-to")
        .arg(format!("{:.3}", req.end_time))
        .arg("-i")
        .arg(source.as_os_str())
        .arg("-c")
        .arg("copy")
        .arg("-movflags")
        .arg("+faststart") // optimize for web playback
        .arg(output_path.as_os_str())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .await
        .map_err(|e| AppError::TrimError(format!("Failed to execute FFmpeg: {e}")))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        info!("FFmpeg stderr: {}", stderr);
        // Clean up failed output
        let _ = tokio::fs::remove_file(&output_path).await;
        return Err(AppError::TrimError(format!(
            "FFmpeg failed ({}): {}",
            output.status,
            stderr.lines().last().unwrap_or("unknown error")
        )));
    }

    // If overwrite mode, atomically rename the temp file over the original
    let final_path = if req.overwrite {
        tokio::fs::rename(&output_path, &source)
            .await
            .map_err(|e| {
                AppError::TrimError(format!("Failed to replace original file: {e}"))
            })?;
        info!("Replaced original file: {:?}", source);
        source.clone()
    } else {
        info!("Saved trimmed file: {:?}", output_path);
        output_path.clone()
    };

    // Build a relative path for the response
    let relative = final_path
        .strip_prefix(media_path)
        .unwrap_or(&final_path)
        .to_string_lossy()
        .to_string();

    Ok(TrimResponse {
        message: if req.overwrite {
            "Original file replaced with trimmed version".into()
        } else {
            "Trimmed video saved".into()
        },
        output_path: relative,
    })
}

/// Generate a unique `_trimmed` filename: video.mp4 -> video_trimmed.mp4, video_trimmed_2.mp4, etc.
fn generate_trimmed_filename(source: &Path) -> Result<PathBuf, AppError> {
    let parent = source
        .parent()
        .ok_or_else(|| AppError::InternalError("Cannot determine parent directory".into()))?;
    let stem = source
        .file_stem()
        .and_then(|s| s.to_str())
        .ok_or_else(|| AppError::InternalError("Cannot determine file stem".into()))?;
    let ext = source
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("mp4");

    // First try: video_trimmed.ext
    let candidate = parent.join(format!("{stem}_trimmed.{ext}"));
    if !candidate.exists() {
        return Ok(candidate);
    }

    // Then: video_trimmed_2.ext, video_trimmed_3.ext, ...
    let mut counter = 2;
    loop {
        let candidate = parent.join(format!("{stem}_trimmed_{counter}.{ext}"));
        if !candidate.exists() {
            return Ok(candidate);
        }
        counter += 1;
    }
}

/// Get the duration of a video in seconds via ffprobe.
pub async fn get_video_duration(path: &Path) -> Result<f64, AppError> {
    let output = Command::new("ffprobe")
        .args([
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
        ])
        .arg(path.as_os_str())
        .output()
        .await
        .map_err(|e| AppError::InternalError(format!("Failed to run ffprobe: {e}")))?;

    if !output.status.success() {
        return Err(AppError::InternalError("ffprobe failed".into()));
    }

    let duration_str = String::from_utf8_lossy(&output.stdout);
    duration_str
        .trim()
        .parse::<f64>()
        .map_err(|_| AppError::InternalError("Failed to parse video duration".into()))
}

/// Generate a thumbnail for a video, cached in the data directory.
/// Attempts HDR tonemapping first for HDR content, falls back to simple extraction.
pub async fn generate_thumbnail(
    video_path: &Path,
    cache_dir: &Path,
) -> Result<PathBuf, AppError> {
    // Create a deterministic cache filename based on path + mtime
    let metadata = std::fs::metadata(video_path)
        .map_err(|e| AppError::NotFound(format!("Cannot stat video: {e}")))?;
    let mtime = metadata
        .modified()
        .unwrap_or(std::time::SystemTime::UNIX_EPOCH);
    let mtime_secs = mtime
        .duration_since(std::time::SystemTime::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();

    let hash_input = format!("{}:{}", video_path.to_string_lossy(), mtime_secs);
    let hash = simple_hash(&hash_input);
    let thumb_path = cache_dir.join(format!("{hash}.avif"));

    // Return cached thumbnail if it exists
    if thumb_path.exists() {
        return Ok(thumb_path);
    }

    // Ensure cache dir exists
    std::fs::create_dir_all(cache_dir)
        .map_err(|e| AppError::InternalError(format!("Failed to create thumb cache: {e}")))?;

    // Chrome's compositor can't handle wide-gamut (P3/BT.2020/HLG) in AVIF thumbnails,
    // producing rendering artifacts that bleed on scroll. We must normalize to BT.709/sRGB.
    //
    // Strategy: try HDR tonemap first (for HLG/PQ content), fall back to colorspace conversion
    // (for P3/wide-gamut SDR), then to simple extraction as last resort.
    // All paths produce lossless AV1 in BT.709 color space.

    let avif_args = [
        "-c:v", "libsvtav1",
        "-crf", "0",
        "-svtav1-params", "lossless=1:fast-decode=1",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-colorspace", "bt709",
    ];

    // Try 1: HDR tonemap (for iPhone HLG / PQ content)
    let hdr_filter = "zscale=t=linear:npl=100,format=gbrpf32le,\
        tonemap=hable:desat=0,\
        zscale=p=bt709:t=bt709:m=bt709:r=tv,\
        format=yuv420p,scale=320:-1";

    let hdr_result = Command::new("ffmpeg")
        .args(["-y", "-ss", "1", "-i"])
        .arg(video_path.as_os_str())
        .args(["-vframes", "1", "-vf", hdr_filter])
        .args(avif_args)
        .arg(thumb_path.as_os_str())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .await;

    if let Ok(status) = hdr_result {
        if status.success() && thumb_path.exists() {
            info!("Generated HDR-tonemapped thumbnail for {:?}", video_path);
            return Ok(thumb_path);
        }
    }

    // Try 2: colorspace conversion (for P3/wide-gamut SDR content like iPhone camera)
    let srgb_filter = "scale=320:-1,format=yuv420p";

    let srgb_result = Command::new("ffmpeg")
        .args(["-y", "-ss", "1", "-i"])
        .arg(video_path.as_os_str())
        .args(["-vframes", "1", "-vf", srgb_filter])
        .args(avif_args)
        .arg(thumb_path.as_os_str())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .await;

    if let Ok(status) = srgb_result {
        if status.success() && thumb_path.exists() {
            info!("Generated sRGB thumbnail for {:?}", video_path);
            return Ok(thumb_path);
        }
    }

    info!("All thumbnail methods failed for {:?}", video_path);
    Err(AppError::InternalError(
        "FFmpeg thumbnail generation failed".into(),
    ))
}

/// Simple hash for cache filenames (not cryptographic, just for deduplication).
fn simple_hash(input: &str) -> String {
    let mut hash: u64 = 5381;
    for byte in input.bytes() {
        hash = hash.wrapping_mul(33).wrapping_add(u64::from(byte));
    }
    format!("{hash:016x}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn path_within_valid() {
        let base = PathBuf::from("/downloads");
        let target = Path::new("/downloads/Clips/video.mp4");
        assert!(ensure_path_within(&base, target).is_ok());
    }

    #[test]
    fn path_within_relative() {
        let base = PathBuf::from("/downloads");
        let target = Path::new("Clips/video.mp4");
        let result = ensure_path_within(&base, target).unwrap();
        assert_eq!(result, PathBuf::from("/downloads/Clips/video.mp4"));
    }

    #[test]
    fn path_traversal_rejected() {
        let base = PathBuf::from("/downloads");
        let target = Path::new("/downloads/../etc/passwd");
        assert!(ensure_path_within(&base, target).is_err());
    }

    #[test]
    fn path_traversal_relative_rejected() {
        let base = PathBuf::from("/downloads");
        let target = Path::new("../etc/passwd");
        assert!(ensure_path_within(&base, target).is_err());
    }

    #[test]
    fn trimmed_filename_no_collision() {
        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("video.mp4");
        std::fs::write(&source, b"test").unwrap();

        let result = generate_trimmed_filename(&source).unwrap();
        assert_eq!(
            result.file_name().unwrap().to_str().unwrap(),
            "video_trimmed.mp4"
        );
    }

    #[test]
    fn trimmed_filename_with_collision() {
        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("video.mp4");
        std::fs::write(&source, b"test").unwrap();
        std::fs::write(dir.path().join("video_trimmed.mp4"), b"existing").unwrap();

        let result = generate_trimmed_filename(&source).unwrap();
        assert_eq!(
            result.file_name().unwrap().to_str().unwrap(),
            "video_trimmed_2.mp4"
        );
    }

    #[test]
    fn trimmed_filename_multiple_collisions() {
        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("video.mp4");
        std::fs::write(&source, b"test").unwrap();
        std::fs::write(dir.path().join("video_trimmed.mp4"), b"e").unwrap();
        std::fs::write(dir.path().join("video_trimmed_2.mp4"), b"e").unwrap();
        std::fs::write(dir.path().join("video_trimmed_3.mp4"), b"e").unwrap();

        let result = generate_trimmed_filename(&source).unwrap();
        assert_eq!(
            result.file_name().unwrap().to_str().unwrap(),
            "video_trimmed_4.mp4"
        );
    }

    #[test]
    fn simple_hash_deterministic() {
        let a = simple_hash("test");
        let b = simple_hash("test");
        assert_eq!(a, b);
        assert_ne!(a, simple_hash("other"));
    }
}
