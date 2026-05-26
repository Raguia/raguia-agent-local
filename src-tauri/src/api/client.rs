use crate::config;
use reqwest::header::{HeaderMap, HeaderValue, AUTHORIZATION};
use serde::{Deserialize, Serialize};
use std::path::Path;
use std::sync::Arc;
use std::sync::RwLock;
use std::time::Duration;
use thiserror::Error;

/// Errors for API operations.
///
/// Each variant includes a user-actionable message in French.
#[derive(Error, Debug)]
pub enum ApiError {
    #[error("Session invalide (401) — reconnectez-vous via l'icone")]
    Unauthorized,

    #[error("Accès refusé (403) — {0}")]
    Forbidden(String),

    #[error("Erreur réseau : {0}")]
    Network(#[from] reqwest::Error),

    #[error("Erreur de configuration : {0}")]
    Config(String),

    #[error("Erreur serveur ({1}) : {0}")]
    Server(String, u16),

    #[error("Réponse JSON invalide : {0}")]
    InvalidResponse(String),

    #[error("Fichier verrouillé — réessayer plus tard : {0}")]
    FileLocked(String),
}

/// Result alias for API operations
pub type ApiResult<T> = Result<T, ApiError>;

// ─── Request / Response types ─────────────────────────────────

/// Login response payload
#[derive(Debug, Deserialize)]
pub struct LoginResponse {
    pub agent_access_token: String,
    #[serde(default)]
    pub token_type: String,
    #[serde(default)]
    pub expires_in_days: Option<f64>,
    #[serde(default)]
    pub expires_at: Option<String>,
    #[serde(default)]
    pub client_slug: Option<String>,
}

/// Sync status from server
#[derive(Debug, Deserialize, Default)]
pub struct SyncStatus {
    #[serde(default)]
    pub sync_requested: bool,
    #[serde(default)]
    pub max_storage_bytes: Option<u64>,
    #[serde(default)]
    pub remote_deletions: Vec<RemoteDeletion>,
    #[serde(default)]
    pub local_agent_enabled: bool,
}

/// A remote deletion instruction
#[derive(Debug, Deserialize)]
pub struct RemoteDeletion {
    pub relative_path: String,
}

/// Upload result field
#[derive(Debug, Deserialize)]
pub struct UploadResult {
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub document_id: Option<String>,
}

/// Upload response payload
#[derive(Debug, Deserialize)]
pub struct UploadResponse {
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub results: Vec<UploadResult>,
    #[serde(default)]
    pub already_existing: Vec<serde_json::Value>,
}

/// Delete-local response
#[derive(Debug, Deserialize)]
pub struct DeleteResponse {
    #[serde(default)]
    pub status: String,
}

/// Sync-complete response
#[derive(Debug, Deserialize)]
pub struct SyncCompleteResponse {
    #[serde(default)]
    pub status: String,
}

/// Refresh-token response
#[derive(Debug, Deserialize)]
pub struct RefreshResponse {
    #[serde(default)]
    pub access_token: Option<String>,
}

/// Version info response
#[derive(Debug, Deserialize)]
pub struct VersionInfo {
    #[serde(default)]
    pub version: Option<String>,
    #[serde(default)]
    pub download_url: Option<String>,
    #[serde(default)]
    pub release_notes: Option<String>,
    #[serde(default)]
    pub mandatory: Option<bool>,
}

/// Metadata for each uploaded file
#[derive(Debug, Serialize)]
pub struct FileMetadata {
    pub relative_path: String,
    pub root_label: String,
    pub external_id: Option<String>,
    pub sync_origin: String,
    pub needs_review: bool,
}

// ─── Configuration constants ──────────────────────────────────

const DEFAULT_TIMEOUT: Duration = Duration::from_secs(30);
const UPLOAD_TIMEOUT: Duration = Duration::from_secs(600);
const MAX_RETRIES: u32 = 3;
const RETRY_BACKOFF: f64 = 2.0;
const RETRYABLE_STATUS: &[u16] = &[429, 500, 502, 503, 504];

/// HTTP client wrapping reqwest for the Raguia portal API.
///
/// Design vs Python agent:
/// - No silent errors: all errors are typed via ``ApiError``
/// - Exponential backoff on transient errors (retry)
/// - Auto-reconnect on 401 using stored password
/// - Token cached in memory to avoid reading encrypted store on every request
/// - Same API endpoints and payload format as Python ``PortalApiClient``
pub struct Client {
    config: Arc<config::Manager>,
    inner: reqwest::Client,
    api_url: RwLock<String>,
    /// Cached auth token (avoids reading encrypted store on every HTTP call)
    cached_token: RwLock<Option<String>>,
}

impl Client {
    /// Create a new API client tied to the agent configuration.
    ///
    /// Loads api_url from config and primes the token cache.
    pub fn new(config: Arc<config::Manager>) -> Self {
        let inner = reqwest::Client::builder()
            .user_agent(concat!("raguia-agent/", env!("CARGO_PKG_VERSION")))
            // Use system CA certificates (like Python certifi)
            .tls_built_in_root_certs(true)
            .timeout(DEFAULT_TIMEOUT)
            .build()
            .expect("Failed to create HTTP client");

        let api_url = RwLock::new(
            config
                .load_config()
                .map(|c| c.api_url.clone())
                .unwrap_or_else(|_| "https://raguia.valentin-fiess.fr".into()),
        );

        // Prime token cache from store (avoids reading encrypted JSON on every request)
        let cached_token = RwLock::new(config.get_token());

        Self {
            config,
            inner,
            api_url,
            cached_token,
        }
    }

    /// Update the API base URL (used after reconfiguration)
    pub fn set_api_url(&self, api_url: &str) {
        if let Ok(mut guard) = self.api_url.write() {
            *guard = api_url.trim_end_matches('/').to_string();
        }
    }

    /// Update auth token in config store AND in-memory cache (after login or refresh).
    ///
    /// Always persists to disk (safe across restarts) AND updates the memory cache
    /// so subsequent HTTP requests avoid reading the encrypted store.
    pub fn set_token(&self, token: &str) -> Result<(), ApiError> {
        // Persist to encrypted store
        self.config
            .set_token(token)
            .map_err(|e| ApiError::Config(e.to_string()))?;
        // Update in-memory cache (fast path for subsequent requests)
        if let Ok(mut guard) = self.cached_token.write() {
            *guard = Some(token.to_string());
        }
        Ok(())
    }

    /// Update password in config (for auto-reconnect)
    pub fn set_password(&self, password: &str) -> Result<(), ApiError> {
        self.config
            .set_password(password)
            .map_err(|e| ApiError::Config(e.to_string()))
    }

    // ─── Auth headers ─────────────────────────────────────────

    /// Build auth headers with the cached token (avoids reading store on every request).
    ///
    /// Falls back to the config store if cache is empty (first call after restart).
    fn auth_headers(&self) -> Result<HeaderMap, ApiError> {
        // Fast path: read from in-memory cache (no I/O)
        let token = self
            .cached_token
            .read()
            .map_err(|_| ApiError::Config("Token cache lock poisoned".into()))?
            .clone()
            // Slow path: fall back to encrypted store (disk I/O)
            .or_else(|| {
                let t = self.config.get_token();
                // Re-prime cache with store value if found
                if let Some(ref t) = t {
                    if let Ok(mut guard) = self.cached_token.write() {
                        *guard = Some(t.clone());
                    }
                }
                t
            })
            .ok_or(ApiError::Unauthorized)?;

        let mut headers = HeaderMap::new();
        let auth_value = format!("Bearer {}", token);
        headers.insert(
            AUTHORIZATION,
            HeaderValue::from_str(&auth_value)
                .map_err(|e| ApiError::Config(format!("Invalid header: {}", e)))?,
        );
        Ok(headers)
    }

    // ─── URL helper ───────────────────────────────────────────

    fn url(&self, path: &str) -> String {
        let api_url = self.api_url.read().unwrap_or_else(|e| e.into_inner());
        format!("{}{}", api_url, path)
    }

    // ─── Retry logic ──────────────────────────────────────────

    /// Execute a request with retry and exponential backoff.
    ///
    /// Only retries on transient errors (network issues, 5xx, 429).
    /// Does NOT retry on 401 (auth) or 403 (forbidden).
    async fn request_with_retry(
        &self,
        method: reqwest::Method,
        url: &str,
        body: Option<serde_json::Value>,
        timeout: Duration,
    ) -> ApiResult<reqwest::Response> {
        let mut delay = Duration::from_secs_f64(RETRY_BACKOFF);
        let mut last_error: Option<ApiError> = None;

        for attempt in 0..=MAX_RETRIES {
            let mut req = self.inner.request(method.clone(), url).timeout(timeout);

            // Add auth headers (only if we have a token)
            if let Ok(headers) = self.auth_headers() {
                req = req.headers(headers);
            }

            if let Some(json) = &body {
                req = req.json(json);
            }

            match req.send().await {
                Ok(resp) => {
                    let status = resp.status();
                    if status.is_success() {
                        return Ok(resp);
                    }

                    // Retry on transient server errors
                    if RETRYABLE_STATUS.contains(&status.as_u16()) && attempt < MAX_RETRIES {
                        tracing::warn!(
                            "HTTP {} from {} (attempt {}/{}), retry in {:.0}s",
                            status,
                            url,
                            attempt + 1,
                            MAX_RETRIES,
                            delay.as_secs_f64(),
                        );
                        tokio::time::sleep(delay).await;
                        delay = delay.mul_f64(RETRY_BACKOFF);
                        continue;
                    }

                    // Map status to error
                    let code = status.as_u16();
                    let detail = Self::extract_detail(resp).await;
                    return match code {
                        401 => Err(ApiError::Unauthorized),
                        403 => Err(ApiError::Forbidden(
                            detail.unwrap_or_else(|| "Accès refusé".into()),
                        )),
                        code => Err(ApiError::Server(
                            detail.unwrap_or_else(|| status.to_string()),
                            code,
                        )),
                    };
                }
                Err(e) if attempt < MAX_RETRIES => {
                    tracing::warn!(
                        "Network error for {} (attempt {}/{}): {}, retry in {:.0}s",
                        url,
                        attempt + 1,
                        MAX_RETRIES,
                        e,
                        delay.as_secs_f64(),
                    );
                    last_error = Some(ApiError::Network(e));
                    tokio::time::sleep(delay).await;
                    delay = delay.mul_f64(RETRY_BACKOFF);
                }
                Err(e) => {
                    return Err(ApiError::Network(e));
                }
            }
        }

        Err(last_error.expect("All retries exhausted but no last_error captured"))
    }

    /// Extract error detail from response body
    async fn extract_detail(resp: reqwest::Response) -> Option<String> {
        let body = resp.bytes().await.ok()?;
        if let Ok(json) = serde_json::from_slice::<serde_json::Value>(&body) {
            if let Some(detail) = json.get("detail") {
                if let Some(s) = detail.as_str() {
                    return Some(s.to_string());
                }
                if let Some(arr) = detail.as_array() {
                    let parts: Vec<String> = arr
                        .iter()
                        .filter_map(|v| {
                            v.get("msg")
                                .and_then(|m| m.as_str())
                                .or_else(|| v.as_str())
                                .map(String::from)
                        })
                        .collect();
                    if !parts.is_empty() {
                        return Some(parts.join("; "));
                    }
                }
            }
        }
        // Fallback: return first 200 chars of raw body
        let text = String::from_utf8_lossy(&body)
            .trim()
            .chars()
            .take(200)
            .collect();
        Some(text)
    }

    // ─── Login ────────────────────────────────────────────────

    /// Authenticate with the Raguia portal using slug + password.
    ///
    /// Corresponds to Python ``portal_agent_login()`` with legacy fallback.
    /// On success, stores the token via ``config.set_token()``.
    pub async fn login(&self, slug: &str, password: &str) -> ApiResult<LoginResponse> {
        let url = self.url("/api/portal/agent/login");
        let body = serde_json::json!({"slug": slug, "password": password});

        let resp = match self
            .request_with_retry(
                reqwest::Method::POST,
                &url,
                Some(body.clone()),
                DEFAULT_TIMEOUT,
            )
            .await
        {
            Ok(r) => r,
            Err(ApiError::Server(_, 404 | 405)) => {
                // Legacy fallback: login portail → issue-token
                return self.legacy_login(slug, password).await;
            }
            Err(e) => return Err(e),
        };

        let payload: LoginResponse = resp
            .json()
            .await
            .map_err(|e| ApiError::InvalidResponse(e.to_string()))?;

        if payload.agent_access_token.is_empty() {
            return Err(ApiError::InvalidResponse(
                "Réponse login sans token agent".into(),
            ));
        }

        self.config
            .set_token(&payload.agent_access_token)
            .map_err(|e| ApiError::Config(e.to_string()))?;

        Ok(payload)
    }

    /// Legacy login flow for backends without /agent/login endpoint.
    /// POST /api/portal/login → POST /api/portal/agent/issue-token
    async fn legacy_login(&self, slug: &str, password: &str) -> ApiResult<LoginResponse> {
        // Step 1: portal login
        let login_url = self.url("/api/portal/login");
        let login_body = serde_json::json!({"slug": slug, "password": password});
        let login_resp = self
            .request_with_retry(
                reqwest::Method::POST,
                &login_url,
                Some(login_body),
                DEFAULT_TIMEOUT,
            )
            .await?;

        let login_payload: serde_json::Value = login_resp
            .json()
            .await
            .map_err(|e| ApiError::InvalidResponse(e.to_string()))?;

        let portal_token = login_payload
            .get("access_token")
            .and_then(|v| v.as_str())
            .ok_or_else(|| ApiError::InvalidResponse("Login réponse sans access_token".into()))?
            .to_string();

        // Step 2: issue agent token
        let issue_url = self.url("/api/portal/agent/issue-token");
        let mut headers = HeaderMap::new();
        headers.insert(
            AUTHORIZATION,
            HeaderValue::from_str(&format!("Bearer {}", portal_token))
                .map_err(|e| ApiError::Config(e.to_string()))?,
        );

        let issue_resp = self
            .inner
            .post(&issue_url)
            .headers(headers)
            .timeout(DEFAULT_TIMEOUT)
            .send()
            .await
            .map_err(ApiError::Network)?;

        if !issue_resp.status().is_success() {
            let code = issue_resp.status().as_u16();
            let detail = Self::extract_detail(issue_resp).await;
            return Err(ApiError::Server(
                detail.unwrap_or_else(|| "issue-token failed".into()),
                code,
            ));
        }

        let issue_payload: serde_json::Value = issue_resp
            .json()
            .await
            .map_err(|e| ApiError::InvalidResponse(e.to_string()))?;

        let token = issue_payload
            .get("access_token")
            .and_then(|v| v.as_str())
            .ok_or_else(|| {
                ApiError::InvalidResponse("issue-token réponse sans access_token".into())
            })?;

        let response = LoginResponse {
            agent_access_token: token.to_string(),
            token_type: issue_payload
                .get("token_type")
                .and_then(|v| v.as_str())
                .unwrap_or("bearer")
                .to_string(),
            expires_in_days: issue_payload
                .get("expires_in_days")
                .and_then(|v| v.as_f64()),
            expires_at: issue_payload
                .get("expires_at")
                .and_then(|v| v.as_str())
                .map(String::from),
            client_slug: Some(slug.to_string()),
        };

        self.config
            .set_token(&response.agent_access_token)
            .map_err(|e| ApiError::Config(e.to_string()))?;

        Ok(response)
    }

    // ─── Sync Status ──────────────────────────────────────────

    /// Get sync status from the server.
    /// Corresponds to Python ``PortalApiClient.sync_status()``
    pub async fn sync_status(&self) -> ApiResult<SyncStatus> {
        let url = self.url("/api/portal/agent/sync-status");
        let resp = self
            .request_with_retry(reqwest::Method::GET, &url, None, Duration::from_secs(60))
            .await?;
        let status: SyncStatus = resp
            .json()
            .await
            .map_err(|e| ApiError::InvalidResponse(e.to_string()))?;
        Ok(status)
    }

    // ─── Upload Files ─────────────────────────────────────────

    /// Upload files with metadata to the server.
    ///
    /// Corresponds to Python ``PortalApiClient.upload_files()``.
    /// Uses multipart form with fields:
    /// - ``metadata_json``: JSON string of metadata array
    /// - ``dry_run``: "true"/"false"
    /// - ``files``: list of file parts (application/octet-stream)
    pub async fn upload_files(
        &self,
        paths: &[&Path],
        metadata: &[FileMetadata],
        dry_run: bool,
    ) -> ApiResult<UploadResponse> {
        if paths.len() != metadata.len() {
            return Err(ApiError::Config(
                "paths et metadata doivent avoir la même longueur".into(),
            ));
        }

        let url = self.url("/api/portal/agent/upload");

        let metadata_json = serde_json::to_string(metadata)
            .map_err(|e| ApiError::Config(format!("metadata serialization: {}", e)))?;

        let mut form = reqwest::multipart::Form::new()
            .text("metadata_json", metadata_json)
            .text("dry_run", dry_run.to_string().to_lowercase());

        for (i, path) in paths.iter().enumerate() {
            let filename = path
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("file")
                .to_string();

            let bytes = tokio::fs::read(path)
                .await
                .map_err(|e| ApiError::FileLocked(format!("{}: {}", path.display(), e)))?;

            let part = reqwest::multipart::Part::bytes(bytes)
                .file_name(filename)
                .mime_str("application/octet-stream")
                .map_err(|e| ApiError::Config(e.to_string()))?;

            form = form.part("files", part);

            if i == 0 && paths.len() > 1 {
                tracing::debug!(
                    "Upload batch: {} files, first={}",
                    paths.len(),
                    path.display()
                );
            }
        }

        let resp = self
            .inner
            .post(&url)
            .multipart(form)
            .headers(self.auth_headers()?)
            .timeout(UPLOAD_TIMEOUT)
            .send()
            .await
            .map_err(ApiError::Network)?;

        if !resp.status().is_success() {
            let code = resp.status().as_u16();
            let status_str = resp.status().to_string();
            let detail = Self::extract_detail(resp).await;
            return match code {
                401 => Err(ApiError::Unauthorized),
                403 => Err(ApiError::Forbidden(
                    detail.unwrap_or_else(|| "Accès refusé".into()),
                )),
                code => Err(ApiError::Server(
                    detail.unwrap_or(status_str),
                    code,
                )),
            };
        }

        let payload: UploadResponse = resp
            .json()
            .await
            .map_err(|e| ApiError::InvalidResponse(e.to_string()))?;

        Ok(payload)
    }

    // ─── Delete Local ─────────────────────────────────────────

    /// Mark a file as deleted on the portal.
    /// Corresponds to Python ``PortalApiClient.delete_local()``
    pub async fn delete_local(&self, relative_path: &str) -> ApiResult<DeleteResponse> {
        let url = self.url("/api/portal/agent/delete-local");
        let body = serde_json::json!({"relative_path": relative_path});

        let resp = self
            .request_with_retry(
                reqwest::Method::POST,
                &url,
                Some(body),
                Duration::from_secs(60),
            )
            .await?;

        let payload: DeleteResponse = resp
            .json()
            .await
            .map_err(|e| ApiError::InvalidResponse(e.to_string()))?;

        Ok(payload)
    }

    // ─── Sync Complete ───────────────────────────────────────

    /// Report sync completion with metrics.
    /// Corresponds to Python ``PortalApiClient.sync_complete()``
    pub async fn sync_complete(
        &self,
        metrics: &serde_json::Value,
        error: Option<&str>,
    ) -> ApiResult<SyncCompleteResponse> {
        let url = self.url("/api/portal/agent/sync-complete");
        let body = serde_json::json!({
            "metrics": metrics,
            "error": error,
        });

        let resp = self
            .request_with_retry(
                reqwest::Method::POST,
                &url,
                Some(body),
                Duration::from_secs(120),
            )
            .await?;

        let payload: SyncCompleteResponse = resp
            .json()
            .await
            .map_err(|e| ApiError::InvalidResponse(e.to_string()))?;

        Ok(payload)
    }

    // ─── Refresh Token ───────────────────────────────────────

    /// Refresh the JWT token.
    /// Corresponds to Python ``PortalApiClient.refresh_token()``
    pub async fn refresh_token(&self) -> ApiResult<String> {
        let url = self.url("/api/portal/agent/refresh-token");

        let resp = self
            .request_with_retry(
                reqwest::Method::POST,
                &url,
                Some(serde_json::json!({})),
                Duration::from_secs(30),
            )
            .await?;

        let payload: RefreshResponse = resp
            .json()
            .await
            .map_err(|e| ApiError::InvalidResponse(e.to_string()))?;

        let token = payload.access_token.ok_or_else(|| {
            ApiError::InvalidResponse("refresh-token réponse sans access_token".into())
        })?;

        self.config
            .set_token(&token)
            .map_err(|e| ApiError::Config(e.to_string()))?;

        Ok(token)
    }

    // ─── Version Info ─────────────────────────────────────────

    /// Get version info for updates.
    /// Corresponds to Python ``PortalApiClient.agent_version_info()``
    pub async fn version_info(&self) -> ApiResult<VersionInfo> {
        let url = self.url("/api/portal/agent/version");

        let resp = self
            .request_with_retry(
                reqwest::Method::GET,
                &url,
                None,
                Duration::from_secs(30),
            )
            .await?;

        let info: VersionInfo = resp
            .json()
            .await
            .map_err(|e| ApiError::InvalidResponse(e.to_string()))?;

        Ok(info)
    }

    // ─── Check Auth ──────────────────────────────────────────

    /// Check if the current token is still valid by calling sync-status.
    /// Returns ``true`` if the server responds OK, ``false`` on 401.
    pub async fn check_auth(&self) -> ApiResult<bool> {
        match self.sync_status().await {
            Ok(_) => Ok(true),
            Err(ApiError::Unauthorized) => Ok(false),
            Err(e) => Err(e),
        }
    }
}
