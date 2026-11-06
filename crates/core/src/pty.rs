use portable_pty::{native_pty_system, CommandBuilder, MasterPty, PtySize};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::sync::{Arc, Mutex};

struct Session {
    master: Box<dyn MasterPty + Send>,
    writer: Box<dyn Write + Send>,
    child: Box<dyn portable_pty::Child + Send + Sync>,
}

#[derive(Default, Clone)]
pub struct PtyManager {
    sessions: Arc<Mutex<HashMap<String, Session>>>,
}

impl PtyManager {
    /// Mở terminal. `cmd` rỗng = shell mặc định. `on_data(id, bytes)` được gọi từ thread đọc.
    pub fn spawn<F>(&self, id: String, cmd: Vec<String>, cols: u16, rows: u16, on_data: F) -> Result<(), String>
    where
        F: Fn(&str, &[u8]) + Send + 'static,
    {
        let pair = native_pty_system()
            .openpty(PtySize { rows, cols, pixel_width: 0, pixel_height: 0 })
            .map_err(|e| e.to_string())?;
        let mut builder = if cmd.is_empty() {
            let shell = std::env::var("SHELL").unwrap_or_else(|_| {
                if cfg!(windows) { "powershell.exe".into() } else { "/bin/bash".into() }
            });
            CommandBuilder::new(shell)
        } else {
            let mut b = CommandBuilder::new(&cmd[0]);
            b.args(&cmd[1..]);
            b
        };
        builder.env("TERM", "xterm-256color");
        if let Some(home) = std::env::var_os("HOME") {
            builder.cwd(home);
        }
        let child = pair.slave.spawn_command(builder).map_err(|e| e.to_string())?;
        drop(pair.slave);
        let mut reader = pair.master.try_clone_reader().map_err(|e| e.to_string())?;
        let writer = pair.master.take_writer().map_err(|e| e.to_string())?;
        self.sessions
            .lock()
            .unwrap()
            .insert(id.clone(), Session { master: pair.master, writer, child });

        let sessions = self.sessions.clone();
        std::thread::spawn(move || {
            let mut buf = [0u8; 8192];
            loop {
                match reader.read(&mut buf) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => on_data(&id, &buf[..n]),
                }
            }
            sessions.lock().unwrap().remove(&id);
            on_data(&id, b"\r\n\x1b[90m[process exited]\x1b[0m\r\n");
        });
        Ok(())
    }

    pub fn write(&self, id: &str, data: &[u8]) -> Result<(), String> {
        let mut s = self.sessions.lock().unwrap();
        let sess = s.get_mut(id).ok_or("session không tồn tại")?;
        sess.writer.write_all(data).map_err(|e| e.to_string())?;
        sess.writer.flush().map_err(|e| e.to_string())
    }

    pub fn resize(&self, id: &str, cols: u16, rows: u16) -> Result<(), String> {
        let s = self.sessions.lock().unwrap();
        let sess = s.get(id).ok_or("session không tồn tại")?;
        sess.master
            .resize(PtySize { rows, cols, pixel_width: 0, pixel_height: 0 })
            .map_err(|e| e.to_string())
    }

    pub fn close(&self, id: &str) {
        if let Some(mut s) = self.sessions.lock().unwrap().remove(id) {
            let _ = s.child.kill();
        }
    }
}
