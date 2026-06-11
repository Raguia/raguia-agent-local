use std::collections::VecDeque;
use std::fs::{File, OpenOptions};
use std::io::{self, Write};
use std::path::Path;
use std::sync::{Arc, Mutex};

const MAX_LOG_ENTRIES: usize = 500;
const MAX_LOG_FILE_BYTES: u64 = 2 * 1024 * 1024; // 2 MB

#[derive(Clone)]
pub struct LogCapture {
    inner: Arc<Mutex<VecDeque<String>>>,
    file: Arc<Mutex<Option<File>>>,
    file_path: Arc<Mutex<Option<std::path::PathBuf>>>,
}

impl LogCapture {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(VecDeque::with_capacity(MAX_LOG_ENTRIES))),
            file: Arc::new(Mutex::new(None)),
            file_path: Arc::new(Mutex::new(None)),
        }
    }

    /// Initialize the persistent log file. Rotates when over MAX_LOG_FILE_BYTES.
    pub fn init_file(&self, path: &Path) -> io::Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        let f = OpenOptions::new().create(true).append(true).open(path)?;
        let mut guard = self.file.lock().unwrap();
        *guard = Some(f);
        *self.file_path.lock().unwrap() = Some(path.to_path_buf());
        Ok(())
    }

    /// Return the path to the persistent log file, if initialized.
    #[allow(dead_code)]
    pub fn file_path(&self) -> Option<std::path::PathBuf> {
        self.file_path.lock().unwrap().clone()
    }

    pub fn get_logs(&self, n: usize) -> Vec<String> {
        let guard = self.inner.lock().unwrap();
        let n = n.min(guard.len());
        guard.iter().rev().take(n).rev().cloned().collect()
    }
}

/// Free function (called from the writer, which holds the same Arcs).
/// Rotates the log file when it exceeds the size cap and reopens it.
fn rotate_if_needed(path: &Path, file_handle: &Arc<Mutex<Option<File>>>) -> io::Result<()> {
    if let Ok(meta) = std::fs::metadata(path) {
        if meta.len() > MAX_LOG_FILE_BYTES {
            let rotated = path.with_extension("log.old");
            let _ = std::fs::rename(path, &rotated);
            let mut guard = file_handle.lock().unwrap();
            *guard = Some(OpenOptions::new().create(true).append(true).open(path)?);
        }
    }
    Ok(())
}

pub struct LogWriter {
    inner: Arc<Mutex<VecDeque<String>>>,
    file: Arc<Mutex<Option<File>>>,
    file_path: Arc<Mutex<Option<std::path::PathBuf>>>,
    in_debug: bool,
}

impl Write for LogWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        // In release builds (windows_subsystem = "windows"), stderr is a null handle.
        // Only write to stderr when actually a TTY (debug build).
        if self.in_debug {
            let _ = io::stderr().write(buf);
        }

        let s = String::from_utf8_lossy(buf);
        let mut guard = self.inner.lock().unwrap();
        for line in s.lines() {
            if !line.is_empty() {
                guard.push_back(line.to_string());
                if guard.len() > MAX_LOG_ENTRIES {
                    guard.pop_front();
                }
            }
        }
        drop(guard);

        // Append to persistent file (best-effort, never fail the writer)
        if let Some(ref mut f) = *self.file.lock().unwrap() {
            let _ = f.write_all(buf);
            let _ = f.flush();
            if let Some(ref p) = *self.file_path.lock().unwrap() {
                let _ = rotate_if_needed(p, &self.file);
            }
        }
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        if self.in_debug {
            let _ = io::stderr().flush();
        }
        if let Some(ref mut f) = *self.file.lock().unwrap() {
            let _ = f.flush();
        }
        Ok(())
    }
}

impl tracing_subscriber::fmt::MakeWriter<'_> for LogCapture {
    type Writer = LogWriter;

    fn make_writer(&self) -> Self::Writer {
        LogWriter {
            inner: self.inner.clone(),
            file: self.file.clone(),
            file_path: self.file_path.clone(),
            in_debug: cfg!(debug_assertions),
        }
    }
}
