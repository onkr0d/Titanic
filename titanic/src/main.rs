use anyhow::Result;
use std::sync::Arc;
use tokio::net::TcpListener;
use tracing::info;
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;

use titanic::auth::FirebaseAuth;
use titanic::config::Config;
use titanic::settings;
use titanic::upload::VideoUploader;
use titanic::AppState;

#[tokio::main]
async fn main() -> Result<()> {
    // Load environment variables from .env file
    dotenvy::dotenv().ok();

    // Load configuration
    let config = Config::from_env()?;

    // Load persisted settings and initialise Sentry (must happen before tracing setup)
    let settings_path = settings::Settings::file_path(&config.data_dir);
    let user_settings = settings::Settings::load(&settings_path);
    let sentry_guard = Arc::new(tokio::sync::Mutex::new(
        settings::init_sentry(&user_settings),
    ));

    // Initialize tracing with Sentry layer
    tracing_subscriber::fmt()
        .finish()
        .with(sentry::integrations::tracing::layer())
        .init();

    info!("Starting Titanic Umbrel server...");
    info!("Configuration loaded: {:?}", config);

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
    let settings_bind_addr = config.settings_bind_address.clone();
    let state = Arc::new(AppState {
        auth,
        uploader,
        data_dir: config.data_dir,
        sentry_guard,
    });

    // Two listeners, two route sets:
    //   * public  — published to the tailnet in compose; every route verifies a token.
    //   * private — no host port mapping; reached only via Umbrel's authenticated
    //               app_proxy, which is what guards the settings page.
    // Splitting at the listener means the settings routes cannot be reached from
    // the published port even if a future route is added carelessly.
    let public_app = titanic::build_public_router(state.clone());
    let private_app = titanic::build_private_router(state);

    println!("Server starting on {bind_addr}");
    info!("Server starting on {bind_addr}");
    info!("Settings server starting on {settings_bind_addr} (not published to the host)");

    let public_listener = TcpListener::bind(&bind_addr).await?;
    let private_listener = TcpListener::bind(&settings_bind_addr).await?;

    // If either listener dies the app is broken, so surface the first failure
    // rather than silently serving half the routes.
    tokio::try_join!(
        async { axum::serve(public_listener, public_app).await },
        async { axum::serve(private_listener, private_app).await },
    )?;

    Ok(())
}
