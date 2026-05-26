use std::collections::VecDeque;
use std::io::{self, Write};
use std::sync::{Arc, Mutex};

const MAX_LOG_ENTRIES: usize = 500;

#[derive(Clone)]
pub struct LogCapture {
    inner: Arc<Mutex<VecDeque<String>>>,
}

impl LogCapture {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(VecDeque::with_capacity(MAX_LOG_ENTRIES))),
        }
    }

    pub fn get_logs(&self, n: usize) -> Vec<String> {
        let guard = self.inner.lock().unwrap();
        let n = n.min(guard.len());
        guard.iter().rev().take(n).rev().cloned().collect()
    }
}

pub struct LogWriter {
    inner: Arc<Mutex<VecDeque<String>>>,
}

impl Write for LogWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let _ = io::stderr().write(buf);
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
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        io::stderr().flush()
    }
}

impl tracing_subscriber::fmt::MakeWriter<'_> for LogCapture {
    type Writer = LogWriter;

    fn make_writer(&self) -> Self::Writer {
        LogWriter {
            inner: self.inner.clone(),
        }
    }
}
