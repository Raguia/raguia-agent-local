// API client for communicating with the Raguia backend server
//
// Handles authentication, file uploads, and status reporting.
// Uses reqwest with automatic token refresh on 401 responses.

mod client;

pub use client::*;
