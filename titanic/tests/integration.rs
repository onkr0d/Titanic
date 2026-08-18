//! Integration tests that spin up the full axum router and exercise
//! endpoints via `tower::ServiceExt::oneshot`.  All tests run with
//! `is_dev: true` in the config so Firebase auth is bypassed.

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
    let (_public, private, tmp) = test_apps(true);
    (private, tmp)
}

/// Build both routers over one shared state, so tests can assert on which
/// routes each listener actually exposes.
/// `bypass_auth` mirrors `DEV_AUTH_BYPASS`; set it false to exercise real
/// token rejection on the public router.
fn test_apps(bypass_auth: bool) -> (Router<()>, Router<()>, tempfile::TempDir) {
    // Create temp dirs for media and data
    let tmp = tempfile::tempdir().unwrap();
    let media_dir = tmp.path().join("media");
    let data_dir = tmp.path().join("data");
    std::fs::create_dir_all(&media_dir).unwrap();
    std::fs::create_dir_all(&data_dir).unwrap();

    let config = titanic::config::Config {
        bind_address: "0.0.0.0:0".into(),
        settings_bind_address: "0.0.0.0:0".into(),
        firebase_project_id: "test-project".into(),
        plex_media_path: media_dir.to_string_lossy().into_owned(),
        is_dev: true,
        dev_auth_bypass: bypass_auth,
        data_dir: data_dir.to_string_lossy().into_owned(),
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

    let public = titanic::build_public_router(state.clone());
    let private = titanic::build_private_router(state);

    (public, private, tmp)
}

/// Convenience: just the tailnet-facing router.
fn public_app() -> (Router<()>, tempfile::TempDir) {
    let (public, _private, tmp) = test_apps(true);
    (public, tmp)
}

async fn status_of(app: Router<()>, method: Method, uri: &str, body: Body) -> StatusCode {
    app.oneshot(
        Request::builder()
            .method(method)
            .uri(uri)
            .header(header::CONTENT_TYPE, "application/json")
            .body(body)
            .unwrap(),
    )
    .await
    .unwrap()
    .status()
}

#[tokio::test]
async fn health_check_returns_200() {
    let (app, _tmp) = public_app();

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


// ---------------------------------------------------------------------------
// Listener split: the settings routes must not be reachable from the published
// port. This is the property that replaces "we assume nobody can reach 3029".
// ---------------------------------------------------------------------------

#[tokio::test]
async fn settings_page_is_absent_from_public_router() {
    // Not 401 — 404. These routes are not mounted on the tailnet-facing listener
    // at all, so no auth bug or missing token check can expose them.
    for uri in ["/", "/settings"] {
        let (app, _tmp) = public_app();
        assert_eq!(
            status_of(app, Method::GET, uri, Body::empty()).await,
            StatusCode::NOT_FOUND,
            "GET {uri} must not exist on the public listener"
        );
    }
}

#[tokio::test]
async fn public_router_refuses_settings_writes() {
    let (public, private, _tmp) = test_apps(true);

    // 405, not 404: the path exists on the public listener as a GET-only
    // redacted view, so axum rejects the method before routing to a handler.
    // What matters is that `put_settings` is not reachable from this listener.
    assert_eq!(
        status_of(
            public,
            Method::PUT,
            "/api/settings",
            Body::from(r#"{"sentry_dsn":"https://evil@example.com/1"}"#)
        )
        .await,
        StatusCode::METHOD_NOT_ALLOWED
    );

    // And prove it: nothing was written. A 405 that still mutated state would
    // be the actual bug, so assert on the stored settings rather than the code.
    let resp = private
        .oneshot(
            Request::builder()
                .uri("/api/settings")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let body = resp.into_body().collect().await.unwrap().to_bytes();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();

    assert!(
        json.get("sentry_dsn").is_none(),
        "public PUT must not reach the settings store, got: {json}"
    );
}

#[tokio::test]
async fn private_router_still_serves_settings_page_and_put() {
    let (_public, private, _tmp) = test_apps(true);

    assert_eq!(
        status_of(private.clone(), Method::GET, "/settings", Body::empty()).await,
        StatusCode::OK
    );
    assert_eq!(
        status_of(
            private,
            Method::PUT,
            "/api/settings",
            Body::from(r#"{"default_folder":"Movies"}"#)
        )
        .await,
        StatusCode::OK
    );
}

#[tokio::test]
async fn public_settings_view_omits_the_sentry_dsn() {
    let (public, private, _tmp) = test_apps(true);

    // Save a DSN through the private router...
    let resp = private
        .oneshot(
            Request::builder()
                .method(Method::PUT)
                .uri("/api/settings")
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(
                    r#"{"sentry_dsn":"https://secret@sentry.io/42","default_folder":"Movies"}"#,
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);

    // ...and confirm the tailnet-facing view exposes the folder but not the DSN.
    let resp = public
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
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();

    assert_eq!(json["default_folder"], "Movies");
    assert!(
        json.get("sentry_dsn").is_none(),
        "sentry_dsn must never cross the tailnet, got: {json}"
    );
}

#[tokio::test]
async fn public_routes_reject_missing_token_without_the_dev_bypass() {
    for uri in ["/api/folders", "/api/settings", "/api/space"] {
        let (public, _private, _tmp) = test_apps(false);
        assert_eq!(
            status_of(public, Method::GET, uri, Body::empty()).await,
            StatusCode::UNAUTHORIZED,
            "{uri} must require a token on the public listener"
        );
    }
}

#[tokio::test]
async fn health_stays_open_on_both_routers() {
    // The container healthcheck polls the public port; Umbrel's app_proxy
    // initialCheck polls the private one. Both must answer without a token.
    let (public, private, _tmp) = test_apps(true);

    for app in [public, private] {
        assert_eq!(
            status_of(app, Method::GET, "/health", Body::empty()).await,
            StatusCode::OK
        );
    }
}
