use crate::error::AppError;
use axum::http::HeaderMap;
use jsonwebtoken::{decode, Algorithm, DecodingKey, Validation};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;
use tracing::info;

use crate::config::Config;

#[derive(Debug, Serialize, Deserialize)]
pub struct FirebaseUser {
    pub uid: String,
    pub email: String,
    pub email_verified: bool,
    pub name: Option<String>,
    pub picture: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct JwtPayload {
    iss: String,
    aud: String,
    auth_time: u64,
    user_id: String,
    sub: String,
    iat: u64,
    exp: u64,
    email: String,
    email_verified: bool,
    name: Option<String>,
    picture: Option<String>,
    firebase: FirebaseClaims,
}

#[derive(Debug, Serialize, Deserialize)]
struct FirebaseClaims {
    sign_in_provider: Option<String>,
    identities: HashMap<String, Vec<String>>,
}

#[derive(Debug, Serialize, Deserialize)]
struct CustomTokenPayload {
    iss: String, // issuer (firebase-adminsdk-...)
    aud: String, // audience
    exp: u64,    // expiration time
    iat: u64,    // issued at
    sub: String, // subject (user ID)
    uid: String, // user ID
}

pub struct FirebaseAuth {
    project_id: String,
    client: Client,
    id_token_keys: Arc<RwLock<HashMap<String, String>>>,
    custom_token_keys: Arc<RwLock<HashMap<String, String>>>,
    is_dev: bool,
}

impl FirebaseAuth {
    pub fn new(config: &Config) -> Result<Self, AppError> {
        let client = Client::new();
        let id_token_keys = Arc::new(RwLock::new(HashMap::new()));
        let custom_token_keys = Arc::new(RwLock::new(HashMap::new()));

        Ok(FirebaseAuth {
            project_id: config.firebase_project_id.clone(),
            client,
            id_token_keys,
            custom_token_keys,
            is_dev: config.is_dev,
        })
    }

    pub async fn verify_token(&self, headers: &HeaderMap) -> Result<FirebaseUser, AppError> {
        // Bypass auth in development mode
        if self.is_dev {
            info!("DEV mode: Bypassing token verification");
            return Ok(FirebaseUser {
                uid: "dev-user".to_string(),
                email: "dev@example.com".to_string(),
                email_verified: true,
                name: Some("Dev User".to_string()),
                picture: None,
            });
        }

        // Extract token from Authorization header
        info!("Verifying token from headers...");
        let auth_header = headers
            .get("Authorization")
            .and_then(|h| h.to_str().ok())
            .ok_or_else(|| {
                info!("Auth Error: No authorization header");
                AppError::AuthError("No authorization header".to_string())
            })?;

        if !auth_header.starts_with("Bearer ") {
            info!("Auth Error: Invalid authorization header format");
            return Err(AppError::AuthError(
                "Invalid authorization header format".to_string(),
            ));
        }

        let token = &auth_header[7..]; // Remove "Bearer " prefix
        info!("Got bearer token, proceeding with verification.");

        // Verify the token
        self.verify_firebase_token(token).await
    }

    async fn verify_firebase_token(&self, token: &str) -> Result<FirebaseUser, AppError> {
        // First, try to verify as a regular Firebase ID token
        match self.verify_id_token(token).await {
            Ok(user) => return Ok(user),
            Err(e) => {
                info!(
                    "ID token verification failed: {}. Trying custom token...",
                    e
                );
            }
        }

        // If ID token verification fails, try to verify as a custom token
        self.verify_custom_token(token).await
    }

    async fn verify_id_token(&self, token: &str) -> Result<FirebaseUser, AppError> {
        // Decode the header to get the key ID
        info!("Decoding token header...");
        let header = jsonwebtoken::decode_header(token)
            .map_err(|e| AppError::AuthError(format!("Invalid token header: {e}")))?;

        let kid = header.kid.ok_or_else(|| {
            info!("Auth Error: No key ID in token");
            AppError::AuthError("No key ID in token".to_string())
        })?;
        info!("Found key ID (kid): {}", kid);

        // Get the public key for ID tokens
        let public_key = self.get_id_token_key(&kid).await?;
        info!("Successfully retrieved ID token public key.");

        // Configure validation
        let mut validation = Validation::new(Algorithm::RS256);
        validation.set_audience(&[self.project_id.clone()]);
        validation.set_issuer(&[format!(
            "https://securetoken.google.com/{}",
            self.project_id
        )]);
        validation.leeway = 60; // Allow for 60 seconds of clock skew

        // Decode and verify the token
        info!("Decoding and validating token...");
        let token_data = decode::<JwtPayload>(
            token,
            &DecodingKey::from_rsa_pem(public_key.as_bytes())
                .map_err(|e| AppError::AuthError(format!("Invalid public key: {e}")))?,
            &validation,
        )
        .map_err(|e| {
            info!("Token verification failed: {}", e);
            AppError::AuthError(format!("Token verification failed: {e}"))
        })?;
        info!("Token decoded and validated successfully.");

        info!(
            "Token verified successfully for user: {}",
            token_data.claims.email
        );
        Ok(FirebaseUser {
            uid: token_data.claims.user_id,
            email: token_data.claims.email,
            email_verified: token_data.claims.email_verified,
            name: token_data.claims.name,
            picture: token_data.claims.picture,
        })
    }

    async fn verify_custom_token(&self, token: &str) -> Result<FirebaseUser, AppError> {
        info!("Attempting to verify as custom token...");

        // Decode the header to get the key ID
        info!("Decoding custom token header...");
        let header = jsonwebtoken::decode_header(token)
            .map_err(|e| AppError::AuthError(format!("Invalid token header: {e}")))?;

        let kid = header.kid.ok_or_else(|| {
            info!("Auth Error: No key ID in custom token");
            AppError::AuthError("No key ID in custom token".to_string())
        })?;
        info!("Found key ID (kid) in custom token: {}", kid);

        // Get the public key for custom tokens (service account keys)
        let public_key = self.get_custom_token_key(&kid).await?;
        info!("Successfully retrieved custom token public key.");

        // Configure validation for custom tokens
        let mut validation = Validation::new(Algorithm::RS256);
        // For custom tokens, the audience must be the Identity Toolkit API
        validation.set_audience(&["https://identitytoolkit.googleapis.com/google.identity.identitytoolkit.v1.IdentityToolkit"]);
        // Set issuer for custom tokens
        validation.set_issuer(&[format!(
            "{}@firebase-adminsdk.iam.gserviceaccount.com",
            self.project_id
        )]);
        validation.leeway = 60; // Allow for 60 seconds of clock skew

        // Decode and verify the token
        info!("Decoding and validating custom token...");
        let token_data = decode::<CustomTokenPayload>(
            token,
            &DecodingKey::from_rsa_pem(public_key.as_bytes())
                .map_err(|e| AppError::AuthError(format!("Invalid public key: {e}")))?,
            &validation,
        )
        .map_err(|e| {
            info!("Custom token verification failed: {}", e);
            AppError::AuthError(format!("Custom token verification failed: {e}"))
        })?;
        info!("Custom token decoded and validated successfully.");
        info!(
            "Custom token verified successfully for user: {}",
            token_data.claims.uid
        );
        Ok(FirebaseUser {
            uid: token_data.claims.uid.clone(),
            email: format!("{}@custom.token", token_data.claims.uid), // Placeholder email for custom tokens
            email_verified: true,
            name: None,
            picture: None,
        })
    }

    async fn get_id_token_key(&self, kid: &str) -> Result<String, AppError> {
        // Check if we have the key cached
        {
            let keys = self.id_token_keys.read().await;
            if let Some(key) = keys.get(kid) {
                info!("Found ID token public key in cache for kid: {}", kid);
                return Ok(key.clone());
            }
        }

        // Fetch and cache all ID token public keys from Firebase if cache is empty or key is not found
        self.refresh_id_token_keys().await?;

        // Try reading from cache again
        {
            let keys = self.id_token_keys.read().await;
            if let Some(key) = keys.get(kid) {
                info!("Found ID token public key in cache for kid: {}", kid);
                return Ok(key.clone());
            }
        }

        // If still not found after refresh, it's an error
        Err(AppError::AuthError(
            "ID token key not found after refresh".to_string(),
        ))
    }

    async fn get_custom_token_key(&self, kid: &str) -> Result<String, AppError> {
        // Check if we have the key cached
        {
            let keys = self.custom_token_keys.read().await;
            if let Some(key) = keys.get(kid) {
                info!("Found custom token public key in cache for kid: {}", kid);
                return Ok(key.clone());
            }
        }

        // Fetch and cache all custom token public keys from Firebase if cache is empty or key is not found
        self.refresh_custom_token_keys().await?;

        // Try reading from cache again
        {
            let keys = self.custom_token_keys.read().await;
            if let Some(key) = keys.get(kid) {
                info!("Found custom token public key in cache for kid: {}", kid);
                return Ok(key.clone());
            }
        }

        // If still not found after refresh, it's an error
        Err(AppError::AuthError(
            "Custom token key not found after refresh".to_string(),
        ))
    }

    async fn refresh_id_token_keys(&self) -> Result<(), AppError> {
        // Fetch ID token public keys from Firebase Auth
        info!("ID token key not in cache, fetching from Google...");
        let url = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com".to_string();

        let response = self
            .client
            .get(&url)
            .send()
            .await
            .map_err(|e| AppError::AuthError(format!("Failed to fetch ID token keys: {e}")))?;

        if !response.status().is_success() {
            info!(
                "Failed to fetch ID token keys from Firebase. Status: {}",
                response.status()
            );
            return Err(AppError::AuthError(
                "Failed to fetch ID token keys from Firebase".to_string(),
            ));
        }
        info!("Successfully fetched ID token keys from Google.");

        let keys_text = response
            .text()
            .await
            .map_err(|e| AppError::AuthError(format!("Failed to read response: {e}")))?;

        // Parse the keys
        let keys_map: HashMap<String, String> = serde_json::from_str(&keys_text)
            .map_err(|e| AppError::AuthError(format!("Failed to parse ID token keys: {e}")))?;

        // Cache all the keys
        {
            let mut keys = self.id_token_keys.write().await;
            *keys = keys_map;
            info!("Cached all ID token keys from Google.");
        }

        Ok(())
    }

    async fn refresh_custom_token_keys(&self) -> Result<(), AppError> {
        // Fetch custom token public keys from Firebase service account
        info!("Custom token key not in cache, fetching from Google...");
        let service_account_email = format!(
            "{}@firebase-adminsdk.iam.gserviceaccount.com",
            self.project_id
        );
        let url =
            format!("https://www.googleapis.com/robot/v1/metadata/x509/{service_account_email}");

        let response =
            self.client.get(&url).send().await.map_err(|e| {
                AppError::AuthError(format!("Failed to fetch custom token keys: {e}"))
            })?;

        if !response.status().is_success() {
            info!(
                "Failed to fetch custom token keys from Firebase. Status: {}",
                response.status()
            );
            return Err(AppError::AuthError(
                "Failed to fetch custom token keys from Firebase".to_string(),
            ));
        }
        info!("Successfully fetched custom token keys from Google.");

        let keys_text = response
            .text()
            .await
            .map_err(|e| AppError::AuthError(format!("Failed to read response: {e}")))?;

        // Parse the keys
        let keys_map: HashMap<String, String> = serde_json::from_str(&keys_text)
            .map_err(|e| AppError::AuthError(format!("Failed to parse custom token keys: {e}")))?;

        // Cache all the keys
        {
            let mut keys = self.custom_token_keys.write().await;
            *keys = keys_map;
            info!("Cached all custom token keys from Google.");
        }

        Ok(())
    }
}
