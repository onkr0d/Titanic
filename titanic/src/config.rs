use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::env;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    pub bind_address: String,
    pub firebase_project_id: String,
    pub plex_media_path: String,
    pub is_dev: bool,
    pub data_dir: String,
}

impl Config {
    pub fn from_env() -> Result<Self> {
        let bind_address = env::var("BIND_ADDRESS").unwrap_or_else(|_| "0.0.0.0:3029".to_string());

        let firebase_project_id = env::var("FIREBASE_PROJECT_ID")
            .context("FIREBASE_PROJECT_ID environment variable is required")?;

        let plex_media_path = env::var("PLEX_MEDIA_PATH").unwrap_or_else(|_| {
            // Use a local path for development on macOS
            if cfg!(target_os = "macos") {
                "./media".to_string()
            } else {
                "/downloads".to_string()
            }
        });

        let is_dev = env::var("IS_DEV")
            .unwrap_or_else(|_| "false".to_string())
            .to_lowercase()
            == "true";

        let data_dir = env::var("DATA_DIR").unwrap_or_else(|_| {
            if cfg!(target_os = "macos") {
                "./data".to_string()
            } else {
                "/data".to_string()
            }
        });

        Ok(Config {
            bind_address,
            firebase_project_id,
            plex_media_path,
            is_dev,
            data_dir,
        })
    }
}
