//! Integration tests that spin up the full axum router and exercise
//! endpoints via `tower::ServiceExt::oneshot`.  All tests run with
//! `IS_DEV=true` so Firebase auth is bypassed.

use axum::body::Body;
use axum::http::{Request, StatusCode, Method, header};
use axum::Router;
use http_body_util::BodyExt;
use tower::ServiceExt;

use std::sync::Arc;
use tokio::sync::Mutex;

// We build the router the same way `main()` does, but pointed at temp dirs.

/// Build a test router with dev-mode auth and temp directories.
/// State is consumed via `.with_state()`, so the router is `Router<()>`.
fn test_app() -> (Router<()>, tempfile::TempDir) {
    // Create temp dirs for media and data
    let tmp = tempfile::tempdir().unwrap();
    let media_dir = tmp.path().join("media");
    let data_dir = tmp.path().join("data");
    std::fs::create_dir_all(&media_dir).unwrap();
    std::fs::create_dir_all(&data_dir).unwrap();

    let config = titanic::config::Config {
        bind_address: "0.0.0.0:0".into(),
        firebase_project_id: "test-project".into(),
        plex_media_path: media_dir.to_str().unwrap().into(),
        is_dev: true,
        data_dir: data_dir.to_str().unwrap().into(),
    };

    let auth = titanic::auth::FirebaseAuth::new(&config).unwrap();
    let uploader = titanic::upload::VideoUploader::new(&config.plex_media_path).unwrap();

    let sentry_guard = Arc::new(Mutex::new(None));

    let state = Arc::new(titanic::AppState {
        auth,
        uploader,
        data_dir: config.data_dir,
        sentry_guard,
    });

    let app = titanic::build_router(state);

    (app, tmp)
}

#[tokio::test]
async fn health_check_returns_200() {
    let (app, _tmp) = test_app();

    let resp = app
        .oneshot(
            Request::builder()
                .uri("/health")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(resp.status(), StatusCode::OK);

    let body = resp.into_body().collect().await.unwrap().to_bytes();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["status"], "healthy");
}

#[tokio::test]
async fn settings_page_returns_html() {
    let (app, _tmp) = test_app();

    let resp = app
        .oneshot(
            Request::builder()
                .uri("/settings")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(resp.status(), StatusCode::OK);
    let ct = resp.headers().get(header::CONTENT_TYPE).unwrap();
    assert!(ct.to_str().unwrap().contains("text/html"));
}

#[tokio::test]
async fn get_settings_returns_json() {
    let (app, _tmp) = test_app();

    let resp = app
        .oneshot(
            Request::builder()
                .uri("/api/settings")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(resp.status(), StatusCode::OK);

    let body = resp.into_body().collect().await.unwrap().to_bytes();
    let _json: serde_json::Value = serde_json::from_slice(&body).unwrap();
}

#[tokio::test]
async fn put_settings_valid_payload() {
    let (app, _tmp) = test_app();

    let payload = serde_json::json!({
        "sentry_dsn": "https://example@sentry.io/123",
        "sentry_traces_sample_rate": 0.5
    });

    let resp = app
        .oneshot(
            Request::builder()
                .method(Method::PUT)
                .uri("/api/settings")
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(serde_json::to_vec(&payload).unwrap()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(resp.status(), StatusCode::OK);
}

#[tokio::test]
async fn put_settings_invalid_rate_returns_400() {
    let (app, _tmp) = test_app();

    let payload = serde_json::json!({
        "sentry_traces_sample_rate": 2.0
    });

    let resp = app
        .oneshot(
            Request::builder()
                .method(Method::PUT)
                .uri("/api/settings")
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(serde_json::to_vec(&payload).unwrap()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn get_folders_returns_json() {
    let (app, _tmp) = test_app();

    let resp = app
        .oneshot(
            Request::builder()
                .uri("/api/folders")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(resp.status(), StatusCode::OK);

    let body = resp.into_body().collect().await.unwrap().to_bytes();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert!(json["folders"].is_array());
}
