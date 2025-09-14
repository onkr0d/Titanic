use crate::error::AppError;
use serde::{Deserialize, Serialize};
use std::fs as std_fs;
use std::path::{Path, PathBuf};
use tokio::fs;
use tracing::info;

pub struct VideoUploader {
    plex_media_path: PathBuf,
}

impl VideoUploader {
    pub fn new(plex_media_path: &str) -> Result<Self, AppError> {
        let path = PathBuf::from(plex_media_path);

        // Ensure the directory exists
        if !path.exists() {
            std_fs::create_dir_all(&path).map_err(|e| {
                AppError::ConfigError(format!(
                    "Failed to create media directory '{plex_media_path}': {e}"
                ))
            })?;
        }

        // Check if path is a directory
        if !path.is_dir() {
            return Err(AppError::ConfigError(format!(
                "Media path '{plex_media_path}' is not a directory"
            )));
        }

        Ok(VideoUploader {
            plex_media_path: path,
        })
    }

    pub async fn upload_video(&self, filename: &str, temp_path: &Path, folder: Option<&str>) -> Result<String, AppError> {
        info!("upload_video called: filename={}, temp_path={:?}, folder={:?}", filename, temp_path, folder);

        // Sanitize filename
        let sanitized_filename = sanitize_filename::sanitize(filename);
        info!("Sanitized filename: {}", sanitized_filename);

        // Always use the Clips directory structure
        let clips_dir = self.plex_media_path.join("Clips");

        // Determine the target directory and generate unique filename
        let (target_dir, _folder_info) = if let Some(folder_name) = folder {
            // Handle "Clips" as special case - save directly to Clips directory
            if folder_name == "Clips" {
                info!("Saving to Clips directory (default)");
                (clips_dir.clone(), "Clips directory".to_string())
            } else {
                // Sanitize folder name as well
                let sanitized_folder = sanitize_filename::sanitize(folder_name);
                let folder_dir = clips_dir.join(&sanitized_folder);

                info!("Creating folder directory: {:?}", folder_dir);
                // Ensure the folder exists
                std_fs::create_dir_all(&folder_dir).map_err(|e| {
                    AppError::InternalError(format!("Failed to create folder '{sanitized_folder}': {e}"))
                })?;

                (folder_dir, format!("folder '{sanitized_folder}'"))
            }
        } else {
            // Fallback: save directly to Clips directory (no subfolder)
            info!("No folder specified, saving to Clips directory");
            (clips_dir.clone(), "Clips directory".to_string())
        };

        // Generate unique filename to prevent overwriting
        // Check for existing files with the same name before generating a unique one
        let potential_path = target_dir.join(&sanitized_filename);
        let file_exists = std_fs::metadata(&potential_path).is_ok();
        info!("Checking if file exists at {:?}: {}", potential_path, file_exists);

        let unique_filename = self.generate_unique_filename(&target_dir, &sanitized_filename);
        let target_path = target_dir.join(&unique_filename);

        if unique_filename != sanitized_filename {
            info!("Generated unique filename: {} (original was {})", unique_filename, sanitized_filename);
        }

        info!("Target path determined: {:?}", target_path);
        info!("Moving file from {:?} to: {:?}", temp_path, target_path);

        // Move the file from the temporary path to the final destination
        fs::copy(&temp_path, &target_path)
            .await
            .map_err(|e| AppError::InternalError(format!("Failed to copy file: {e}")))?;

        fs::remove_file(&temp_path)
            .await
            .map_err(|e| AppError::InternalError(format!("Failed to remove temporary file: {e}")))?;


        Ok(target_path.to_string_lossy().to_string())
    }

    // Generate a unique filename by appending counter if file already exists
    fn generate_unique_filename(&self, directory: &Path, filename: &str) -> String {
        let path = directory.join(filename);

        // Use std_fs (std::fs) instead of fs (tokio::fs) since this is a synchronous function
        if std_fs::metadata(&path).is_err() {
            return filename.to_string();
        }

        // Split filename into base and extension
        let (base, ext) = if let Some(dot_pos) = filename.rfind('.') {
            let (base_part, ext_part) = filename.split_at(dot_pos);
            (base_part, ext_part)
        } else {
            (filename, "")
        };

        // Try appending counter until we find a unique filename
        let mut counter = 1;
        loop {
            let new_filename = if ext.is_empty() {
                format!("{base}_{counter}")
            } else {
                format!("{base}_{counter}{ext}")
            };

            let new_path = directory.join(&new_filename);
            if std_fs::metadata(&new_path).is_err() {
                return new_filename;
            }
            counter += 1;
        }
    }

    pub async fn get_space_info(&self) -> Result<SpaceInfo, AppError> {
        let path_str = self.plex_media_path.to_str().ok_or_else(|| {
            AppError::InternalError("Plex media path is not valid UTF-8".to_string())
        })?;
        let (total, used, free) = disk_space::get(path_str)?;
        Ok(SpaceInfo { total, used, free })
    }

    pub async fn list_folders(&self) -> Result<Vec<String>, AppError> {
        let clips_dir = self.plex_media_path.join("Clips");

        // Ensure Clips directory exists
        std_fs::create_dir_all(&clips_dir).map_err(|e| {
            AppError::InternalError(format!("Failed to create Clips directory: {e}"))
        })?;

        // Read the directory entries
        let mut folders = Vec::new();
        let entries = std_fs::read_dir(&clips_dir).map_err(|e| {
            AppError::InternalError(format!("Failed to read Clips directory: {e}"))
        })?;

        for entry in entries {
            let entry = entry.map_err(|e| {
                AppError::InternalError(format!("Failed to read directory entry: {e}"))
            })?;

            // Only include directories
            if entry.file_type().map_err(|e| {
                AppError::InternalError(format!("Failed to get file type: {e}"))
            })?.is_dir() {
                if let Some(folder_name) = entry.file_name().to_str() {
                    folders.push(folder_name.to_string());
                }
            }
        }

        // Sort folders alphabetically
        folders.sort();

        Ok(folders)
    }
}

mod disk_space {
    use crate::error::AppError;
    use std::process::Command;

    pub fn get(path: &str) -> Result<(u64, u64, u64), AppError> {
        let output = Command::new("df")
            .arg("-k") // Use 1K blocks for POSIX compatibility
            .arg(path)
            .output()
            .map_err(|e| {
                AppError::InternalError(format!("Failed to execute 'df' command: {e}"))
            })?;

        if !output.status.success() {
            return Err(AppError::InternalError(format!(
                "'df' command failed with error: {}",
                String::from_utf8_lossy(&output.stderr)
            )));
        }

        let output_str = String::from_utf8_lossy(&output.stdout);
        let lines: Vec<&str> = output_str.trim().split('\n').collect();

        if lines.len() < 2 {
            return Err(AppError::InternalError(
                "Unexpected 'df' output format".to_string(),
            ));
        }

        let parts: Vec<&str> = lines[1].split_whitespace().collect();
        if parts.len() < 4 {
            return Err(AppError::InternalError(
                "Unexpected 'df' output format on value line".to_string(),
            ));
        }

        let total = parts[1]
            .parse::<u64>()
            .map_err(|_| AppError::InternalError("Failed to parse total space".to_string()))?
            * 1024; // Convert from 1K-blocks to bytes
        let used = parts[2]
            .parse::<u64>()
            .map_err(|_| AppError::InternalError("Failed to parse used space".to_string()))?
            * 1024;
        let free = parts[3]
            .parse::<u64>()
            .map_err(|_| AppError::InternalError("Failed to parse free space".to_string()))?
            * 1024;

        Ok((total, used, free))
    }
}

#[derive(Debug, Serialize, Deserialize)]
pub struct SpaceInfo {
    pub total: u64,
    pub used: u64,
    pub free: u64,
}
