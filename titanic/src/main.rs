use anyhow::Result;
use std::sync::Arc;
use tokio::net::TcpListener;
use tracing::info;

use titanic::auth::FirebaseAuth;
use titanic::config::Config;
use titanic::settings;
use titanic::upload::VideoUploader;
use titanic::AppState;

#[tokio::main]
async fn main() -> Result<()> {
    // Load environment variables from .env file
    dotenvy::dotenv().ok();

    // Initialize tracing
    tracing_subscriber::fmt::init();

    info!("Starting Titanic Umbrel server...");

    // Load configuration
    let config = Config::from_env()?;
    info!("Configuration loaded: {:?}", config);

    // Load persisted settings and initialise Sentry
    let settings_path = settings::Settings::file_path(&config.data_dir);
    let user_settings = settings::Settings::load(&settings_path);
    let sentry_guard = Arc::new(tokio::sync::Mutex::new(
        settings::init_sentry(&user_settings),
    ));

    // Initialize Firebase authentication
    let auth = FirebaseAuth::new(&config)?;
    info!("Firebase authentication initialized");

    // Initialize video uploader
    let uploader = VideoUploader::new(&config.plex_media_path)?;
    info!(
        "Video uploader initialized with Plex path: {}",
        config.plex_media_path
    );

    // Create shared state
    let bind_addr = config.bind_address.clone();
    let state = Arc::new(AppState {
        auth,
        uploader,
        data_dir: config.data_dir,
        sentry_guard,
    });

    // Build router
    let app = titanic::build_router(state);

    println!("Server starting on {bind_addr}");
    info!("Server starting on {bind_addr}");

    // Start server
    let listener = TcpListener::bind(&bind_addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
