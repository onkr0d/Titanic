use axum::{
    Json,
    extract::State,
    http::{StatusCode, header},
    response::IntoResponse,
};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tokio::sync::Mutex;
use tracing::{info, warn};

use crate::AppState;

// ---------------------------------------------------------------------------
// Settings struct
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct Settings {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sentry_dsn: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sentry_environment: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sentry_traces_sample_rate: Option<f32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub default_folder: Option<String>,
}

impl Settings {
    /// Load settings from a JSON file.  Returns `Default` when the file does
    /// not exist or cannot be parsed.
    pub fn load(path: &Path) -> Self {
        match std::fs::read_to_string(path) {
            Ok(contents) => serde_json::from_str(&contents).unwrap_or_else(|e| {
                warn!("Failed to parse settings file: {e}; using defaults");
                Self::default()
            }),
            Err(_) => Self::default(),
        }
    }

    /// Persist settings to a JSON file, creating parent dirs if needed.
    pub fn save(&self, path: &Path) -> anyhow::Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let json = serde_json::to_string_pretty(self)?;
        std::fs::write(path, json)?;
        Ok(())
    }

    /// Resolve the path to the settings file for a given data directory.
    pub fn file_path(data_dir: &str) -> PathBuf {
        Path::new(data_dir).join("settings.json")
    }
}

// ---------------------------------------------------------------------------
// Sentry initialisation
// ---------------------------------------------------------------------------

pub type SentryGuard = Arc<Mutex<Option<sentry::ClientInitGuard>>>;

/// Initialise (or re-initialise) the Sentry SDK from the given settings,
/// falling back to environment variables for any field not set.
pub fn init_sentry(settings: &Settings) -> Option<sentry::ClientInitGuard> {
    let dsn = settings
        .sentry_dsn
        .as_deref()
        .filter(|s| !s.is_empty())
        .map(String::from)
        .or_else(|| std::env::var("SENTRY_DSN").ok().filter(|s| !s.is_empty()));

    let dsn = dsn?;

    let environment = settings
        .sentry_environment
        .clone()
        .or_else(|| std::env::var("SENTRY_ENVIRONMENT").ok());

    let traces_sample_rate = settings
        .sentry_traces_sample_rate
        .or_else(|| {
            std::env::var("SENTRY_TRACES_SAMPLE_RATE")
                .ok()
                .and_then(|v| {
                    v.trim().parse::<f32>().ok().or_else(|| {
                        warn!("Invalid SENTRY_TRACES_SAMPLE_RATE={v}; ignoring");
                        None
                    })
                })
        })
        .unwrap_or(1.0);

    info!("Initialising Sentry (environment={environment:?}, traces_sample_rate={traces_sample_rate})");

    Some(sentry::init((
        dsn,
        sentry::ClientOptions {
            release: sentry::release_name!(),
            environment: environment.map(|v| v.into()),
            send_default_pii: true,
            traces_sample_rate,
            ..Default::default()
        },
    )))
}

/// Re-initialise Sentry with new settings and store the guard.
pub async fn reinit_sentry(settings: &Settings, guard: &SentryGuard) {
    let mut g = guard.lock().await;
    // Drop the old guard (shuts down the old client).
    *g = None;
    // Init with the new settings.
    *g = init_sentry(settings);
}

// ---------------------------------------------------------------------------
// Route handlers
// ---------------------------------------------------------------------------

static SETTINGS_HTML: &str = include_str!("settings.html");

/// `GET /settings` — serve the settings page.
pub async fn settings_page() -> impl IntoResponse {
    (
        StatusCode::OK,
        [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
        SETTINGS_HTML,
    )
}

/// `GET /api/settings` — return current saved settings as JSON.
pub async fn get_settings(
    State(state): State<Arc<AppState>>,
) -> Json<Settings> {
    let path = Settings::file_path(&state.data_dir);
    Json(Settings::load(&path))
}

/// `PUT /api/settings` — save settings and hot-reload Sentry.
pub async fn put_settings(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<Settings>,
) -> Result<Json<Settings>, (StatusCode, Json<serde_json::Value>)> {
    // Validate traces sample rate if provided.
    if let Some(rate) = payload.sentry_traces_sample_rate {
        if !(0.0..=1.0).contains(&rate) {
            return Err((
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": "sentry_traces_sample_rate must be between 0.0 and 1.0"
                })),
            ));
        }
    }

    let path = Settings::file_path(&state.data_dir);

    payload.save(&path).map_err(|e| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": format!("Failed to save settings: {e}") })),
        )
    })?;

    info!("Settings saved to {}", path.display());

    // Hot-reload Sentry.
    reinit_sentry(&payload, &state.sentry_guard).await;

    Ok(Json(payload))
}
