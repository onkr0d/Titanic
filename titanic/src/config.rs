use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::env;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    pub bind_address: String,
    /// Bind address for the settings listener. This port is deliberately NOT
    /// published to the host in `docker-compose.yml` — Umbrel's authenticated
    /// app_proxy reaches it over the app network, and nothing else can. Keeping
    /// it off the published port is what makes the settings page local-only.
    pub settings_bind_address: String,
    pub firebase_project_id: String,
    pub plex_media_path: String,
    pub is_dev: bool,
    /// Whether to bypass Firebase auth. Deliberately separate from `is_dev` so a
    /// stray `IS_DEV=true` in production can't silently disable authentication;
    /// requires both `IS_DEV=true` and an explicit `DEV_AUTH_BYPASS=true`.
    pub dev_auth_bypass: bool,
    pub data_dir: String,
}

impl Config {
    pub fn from_env() -> Result<Self> {
        let bind_address = env::var("BIND_ADDRESS").unwrap_or_else(|_| "0.0.0.0:3029".to_string());

        // 0.0.0.0 here is safe and required: it is all interfaces *of the container's
        // network namespace*, which is how app_proxy reaches it. Host exposure is
        // controlled by the `ports:` list in compose, which omits this port.
        let settings_bind_address =
            env::var("SETTINGS_BIND_ADDRESS").unwrap_or_else(|_| "0.0.0.0:3031".to_string());

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

        let dev_auth_bypass = is_dev
            && env::var("DEV_AUTH_BYPASS")
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
            settings_bind_address,
            firebase_project_id,
            plex_media_path,
            is_dev,
            dev_auth_bypass,
            data_dir,
        })
    }
}
