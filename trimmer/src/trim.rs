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
    /// Audio-relative stream indices to keep; None keeps all streams.
    #[serde(default)]
    pub tracks: Option<Vec<u32>>,
}

#[derive(Debug, Serialize)]
pub struct AudioTrack {
    pub index: u32,
    pub title: Option<String>,
    pub language: Option<String>,
    pub codec: Option<String>,
    pub default: bool,
}

/// List audio streams via ffprobe. `index` is the audio-relative index
/// usable in `-map 0:a:{index}`.
pub async fn list_audio_tracks(path: &Path) -> Result<Vec<AudioTrack>, AppError> {
    let output = Command::new("ffprobe")
        .args([
            "-v", "error",
            "-select_streams", "a",
            "-show_entries",
            "stream=codec_name:stream_tags=title,name,language:stream_disposition=default",
            "-of", "json",
        ])
        .arg(path.as_os_str())
        .output()
        .await
        .map_err(|e| AppError::InternalError(format!("Failed to run ffprobe: {e}")))?;

    if !output.status.success() {
        return Err(AppError::InternalError("ffprobe failed".into()));
    }

    let parsed: serde_json::Value = serde_json::from_slice(&output.stdout)
        .map_err(|e| AppError::InternalError(format!("Failed to parse ffprobe output: {e}")))?;

    let streams = parsed["streams"].as_array().cloned().unwrap_or_default();
    Ok(streams
        .iter()
        .enumerate()
        .map(|(i, s)| AudioTrack {
            index: i as u32,
            // mp4 muxers store the stream title under "name", mkv under "title"
            title: s["tags"]["title"]
                .as_str()
                .or_else(|| s["tags"]["name"].as_str())
                .map(str::to_string),
            language: s["tags"]["language"].as_str().map(str::to_string),
            codec: s["codec_name"].as_str().map(str::to_string),
            default: s["disposition"]["default"].as_i64() == Some(1),
        })
        .collect())
}

/// Build `-map` args for a trim. Without an explicit selection ffmpeg keeps
/// only one audio stream, so default to `-map 0` (everything).
fn build_map_args(tracks: Option<&[u32]>) -> Vec<String> {
    let Some(tracks) = tracks else {
        return vec!["-map".into(), "0".into()];
    };
    let mut args = vec!["-map".into(), "0:v:0".into()];
    for t in tracks {
        args.push("-map".into());
        args.push(format!("0:a:{t}"));
    }
    // First kept track becomes default so players pick it automatically
    args.push("-disposition:a:0".into());
    args.push("default".into());
    args
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
    if req.tracks.as_ref().is_some_and(|t| t.is_empty()) {
        return Err(AppError::BadRequest(
            "At least one audio track must be selected".into(),
        ));
    }

    // mp4's muxer writes stream titles from the "title" key but its demuxer
    // exposes them as "name", so a plain -c copy remux drops them; re-apply.
    let source_tracks = match list_audio_tracks(&source).await {
        Ok(t) => t,
        Err(e) if req.tracks.is_some() => return Err(e),
        Err(_) => Vec::new(),
    };
    let kept_tracks: Vec<&AudioTrack> = match &req.tracks {
        Some(selection) => {
            let kept: Vec<&AudioTrack> = selection
                .iter()
                .filter_map(|&i| source_tracks.get(i as usize))
                .collect();
            if kept.len() != selection.len() {
                return Err(AppError::BadRequest("Invalid audio track index".into()));
            }
            kept
        }
        None => source_tracks.iter().collect(),
    };
    let mut metadata_args: Vec<String> = Vec::new();
    for (out_idx, track) in kept_tracks.iter().enumerate() {
        if let Some(title) = &track.title {
            metadata_args.push(format!("-metadata:s:a:{out_idx}"));
            metadata_args.push(format!("title={title}"));
        }
        if let Some(lang) = &track.language {
            metadata_args.push(format!("-metadata:s:a:{out_idx}"));
            metadata_args.push(format!("language={lang}"));
        }
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
        .args(build_map_args(req.tracks.as_deref()))
        .args(&metadata_args)
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
    let thumb_path = cache_dir.join(cache_filename(video_path, "", "avif")?);

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
    // Strategy: try HDR tonemap first (for HLG/PQ content), then fall back to colorspace
    // conversion (for P3/wide-gamut SDR).
    // Both paths produce lossless AV1 in BT.709 color space.

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

/// Extract a single audio track to an m4a, cached in the data directory.
/// Stream-copies when possible, falls back to AAC for codecs mp4 can't hold.
pub async fn extract_audio_track(
    video_path: &Path,
    track: u32,
    cache_dir: &Path,
) -> Result<PathBuf, AppError> {
    let out_path = cache_dir.join(cache_filename(video_path, &format!(":a{track}"), "m4a")?);

    if out_path.exists() {
        return Ok(out_path);
    }

    std::fs::create_dir_all(cache_dir)
        .map_err(|e| AppError::InternalError(format!("Failed to create audio cache: {e}")))?;

    // Extract to a per-call temp file and atomically rename into place, so
    // concurrent requests for the same track never write or serve a partial file.
    let unique = format!(
        "{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::SystemTime::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    );
    let tmp_path = cache_dir.join(format!(".{unique}.tmp.m4a"));

    let codec_attempts: [&[&str]; 2] = [&["-c:a", "copy"], &["-c:a", "aac", "-b:a", "192k"]];
    for codec_args in codec_attempts {
        let status = Command::new("ffmpeg")
            .args(["-y", "-i"])
            .arg(video_path.as_os_str())
            .arg("-map")
            .arg(format!("0:a:{track}"))
            .arg("-vn")
            .args(codec_args)
            .args(["-movflags", "+faststart"])
            .arg(tmp_path.as_os_str())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .kill_on_drop(true) // don't leave orphaned ffmpeg when the request is canceled
            .status()
            .await;

        if let Ok(s) = status
            && s.success()
            && tmp_path.exists()
            && std::fs::rename(&tmp_path, &out_path).is_ok()
        {
            info!("Extracted audio track {} from {:?}", track, video_path);
            return Ok(out_path);
        }
        let _ = std::fs::remove_file(&tmp_path);
    }

    Err(AppError::InternalError(format!(
        "Failed to extract audio track {track}"
    )))
}

/// Deterministic cache filename from a source file's path + mtime, plus an
/// `extra` discriminator (e.g. an audio track index). Keyed on mtime so the
/// entry naturally invalidates when the source file changes.
fn cache_filename(source: &Path, extra: &str, ext: &str) -> Result<String, AppError> {
    let metadata = std::fs::metadata(source)
        .map_err(|e| AppError::NotFound(format!("Cannot stat file: {e}")))?;
    let mtime_secs = metadata
        .modified()
        .unwrap_or(std::time::SystemTime::UNIX_EPOCH)
        .duration_since(std::time::SystemTime::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let hash_input = format!("{}:{}{}", source.to_string_lossy(), mtime_secs, extra);
    Ok(format!("{}.{ext}", simple_hash(&hash_input)))
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

    #[test]
    fn map_args_default_keeps_everything() {
        assert_eq!(build_map_args(None), vec!["-map", "0"]);
    }

    #[test]
    fn map_args_selected_tracks() {
        assert_eq!(
            build_map_args(Some(&[0, 2])),
            vec![
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-map",
                "0:a:2",
                "-disposition:a:0",
                "default"
            ]
        );
    }

    /// Create a test video with 3 stereo audio tracks (like pipeline output).
    async fn make_multitrack_video(path: &Path) -> bool {
        let status = tokio::process::Command::new("ffmpeg")
            .args([
                "-y",
                "-f", "lavfi", "-i", "testsrc=duration=2:size=128x72:rate=10",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=1320:duration=2",
                "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:a",
                "-c:v", "mpeg4", "-c:a", "aac",
                "-metadata:s:a:0", "title=Default mix",
                "-metadata:s:a:1", "title=System only (raw)",
                "-metadata:s:a:2", "title=Mic only (raw)",
                "-disposition:a:0", "default",
            ])
            .arg(path.as_os_str())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .await;
        matches!(status, Ok(s) if s.success())
    }

    fn ffmpeg_available() -> bool {
        std::process::Command::new("ffmpeg")
            .arg("-version")
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .is_ok()
    }

    #[tokio::test]
    async fn trim_keeps_all_audio_tracks() {
        if !ffmpeg_available() {
            eprintln!("ffmpeg not available, skipping");
            return;
        }
        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("multi.mp4");
        assert!(make_multitrack_video(&source).await);

        let req = TrimRequest {
            path: "multi.mp4".into(),
            start_time: 0.0,
            end_time: 1.0,
            overwrite: false,
            tracks: None,
        };
        let res = trim_video(dir.path(), &req).await.unwrap();

        let tracks = list_audio_tracks(&dir.path().join(&res.output_path))
            .await
            .unwrap();
        assert_eq!(tracks.len(), 3);
        assert_eq!(tracks[0].title.as_deref(), Some("Default mix"));
        assert!(tracks[0].default);
        assert_eq!(tracks[2].title.as_deref(), Some("Mic only (raw)"));
    }

    #[tokio::test]
    async fn trim_keeps_selected_tracks_and_sets_default() {
        if !ffmpeg_available() {
            eprintln!("ffmpeg not available, skipping");
            return;
        }
        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("multi.mp4");
        assert!(make_multitrack_video(&source).await);

        let req = TrimRequest {
            path: "multi.mp4".into(),
            start_time: 0.0,
            end_time: 1.0,
            overwrite: false,
            tracks: Some(vec![1, 2]),
        };
        let res = trim_video(dir.path(), &req).await.unwrap();

        let tracks = list_audio_tracks(&dir.path().join(&res.output_path))
            .await
            .unwrap();
        assert_eq!(tracks.len(), 2);
        assert_eq!(tracks[0].title.as_deref(), Some("System only (raw)"));
        assert!(tracks[0].default);
        assert!(!tracks[1].default);
    }

    #[tokio::test]
    async fn extract_track_cached() {
        if !ffmpeg_available() {
            eprintln!("ffmpeg not available, skipping");
            return;
        }
        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("multi.mp4");
        assert!(make_multitrack_video(&source).await);

        let cache = dir.path().join("audio-cache");
        let first = extract_audio_track(&source, 1, &cache).await.unwrap();
        assert!(first.exists());
        let mtime = std::fs::metadata(&first).unwrap().modified().unwrap();

        // Second call must hit the cache, not re-extract
        let second = extract_audio_track(&source, 1, &cache).await.unwrap();
        assert_eq!(first, second);
        assert_eq!(std::fs::metadata(&second).unwrap().modified().unwrap(), mtime);
    }

    #[tokio::test]
    async fn concurrent_extract_same_track_yields_valid_files() {
        if !ffmpeg_available() {
            eprintln!("ffmpeg not available, skipping");
            return;
        }
        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("multi.mp4");
        assert!(make_multitrack_video(&source).await);
        let cache = dir.path().join("audio-cache");

        // Race several extractions of the same track; atomic publish must ensure
        // every returned path is a fully-muxed, probeable file (no partial serve).
        let handles: Vec<_> = (0..6)
            .map(|_| {
                let src = source.clone();
                let cache = cache.clone();
                tokio::spawn(async move { extract_audio_track(&src, 1, &cache).await })
            })
            .collect();

        for h in handles {
            let path = h.await.unwrap().unwrap();
            assert!(path.exists());
            // A partial/corrupt m4a would have no probeable audio stream.
            let tracks = list_audio_tracks(&path).await.unwrap();
            assert_eq!(tracks.len(), 1);
        }
        // Only the final cache file should remain — no leftover temp files.
        let leftovers: Vec<_> = std::fs::read_dir(&cache)
            .unwrap()
            .filter_map(|e| e.ok())
            .filter(|e| e.file_name().to_string_lossy().contains(".tmp."))
            .collect();
        assert!(leftovers.is_empty(), "temp files left behind: {leftovers:?}");
    }

    #[tokio::test]
    async fn empty_track_selection_rejected() {
        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("video.mp4");
        std::fs::write(&source, b"test").unwrap();

        let req = TrimRequest {
            path: "video.mp4".into(),
            start_time: 0.0,
            end_time: 1.0,
            overwrite: false,
            tracks: Some(vec![]),
        };
        assert!(trim_video(dir.path(), &req).await.is_err());
    }
}
