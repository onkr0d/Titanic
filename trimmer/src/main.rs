use anyhow::Result;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::net::TcpListener;
use tracing::info;

use trimmer::AppState;

#[tokio::main]
async fn main() -> Result<()> {
    // Load environment variables from .env file
    dotenvy::dotenv().ok();

    // Initialize tracing
    tracing_subscriber::fmt::init();

    info!("Starting Titanic Trimmer...");

    // Load configuration from environment
    let bind_address =
        std::env::var("BIND_ADDRESS").unwrap_or_else(|_| "0.0.0.0:3030".to_string());

    let media_path = std::env::var("MEDIA_PATH").unwrap_or_else(|_| {
        if cfg!(target_os = "macos") {
            "./media".to_string()
        } else {
            "/downloads".to_string()
        }
    });

    let data_dir = std::env::var("DATA_DIR").unwrap_or_else(|_| {
        if cfg!(target_os = "macos") {
            "./data".to_string()
        } else {
            "/data".to_string()
        }
    });

    let media_path = PathBuf::from(&media_path);
    let data_dir = PathBuf::from(&data_dir);

    // Ensure directories exist
    std::fs::create_dir_all(&media_path)?;
    std::fs::create_dir_all(&data_dir)?;

    info!("Media path: {:?}", media_path);
    info!("Data directory: {:?}", data_dir);

    // Create shared state
    let state = Arc::new(AppState {
        media_path,
        data_dir,
    });

    // Build router
    let app = trimmer::build_router(state.clone());

    // Spawn background thumbnail pre-generation with periodic re-scan
    let bg_state = state.clone();
    tokio::spawn(async move {
        // Small delay to let the server finish binding first
        tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;
        loop {
            trimmer::pre_generate_thumbnails(bg_state.clone()).await;
            // Re-scan every 5 minutes to catch newly added videos
            tokio::time::sleep(tokio::time::Duration::from_secs(300)).await;
        }
    });

    info!("Server starting on {bind_addr}", bind_addr = bind_address);

    // Start server
    let listener = TcpListener::bind(&bind_address).await?;
    axum::serve(listener, app).await?;

    Ok(())
}
