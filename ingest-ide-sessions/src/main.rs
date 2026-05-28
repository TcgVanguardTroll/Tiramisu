use rayon::prelude::*;
use rusqlite::{params, Connection};
use serde::Deserialize;
use std::{
    collections::HashSet,
    env,
    fs,
    path::{Path, PathBuf},
    sync::Mutex,
    time::Instant,
};
use walkdir::WalkDir;

const IDE_BASE: &str = "Library/Application Support/Kiro/User/globalStorage/kiro.kiroagent";

#[derive(Deserialize)]
struct ChatFile {
    #[serde(default)]
    chat: Vec<ChatMessage>,
    #[serde(rename = "executionId", default)]
    execution_id: Option<String>,
}

#[derive(Deserialize)]
struct ChatMessage {
    role: String,
    #[allow(dead_code)]
    content: String,
}

struct SessionRecord {
    session_id: String,
    workspace_hash: String,
    message_count: i64,
    human_count: i64,
}

fn main() {
    let start = Instant::now();

    let home = env::var("HOME").expect("HOME not set");
    let ide_dir = PathBuf::from(&home).join(IDE_BASE);

    // Default DB path or accept as arg
    let db_path = env::args().nth(1).unwrap_or_else(|| {
        env::var("TIRAMISU_DB").unwrap_or_else(|_| {
            // Look relative to the binary, or fallback
            let fallback = PathBuf::from(&home)
                .join("tiramisu/tiramisu.db");
            fallback.to_string_lossy().to_string()
        })
    });

    if !ide_dir.exists() {
        eprintln!("Kiro IDE directory not found: {}", ide_dir.display());
        std::process::exit(1);
    }

    // Collect all .chat file paths
    let chat_files: Vec<PathBuf> = WalkDir::new(&ide_dir)
        .min_depth(2)
        .max_depth(2)
        .into_iter()
        .filter_map(|e| e.ok())
        .filter(|e| e.path().extension().map_or(false, |ext| ext == "chat"))
        .map(|e| e.into_path())
        .collect();

    eprintln!("Found {} .chat files", chat_files.len());

    // Load existing session IDs to skip
    let conn = Connection::open(&db_path).expect("Failed to open tiramisu.db");
    let existing: HashSet<String> = {
        let mut stmt = conn
            .prepare("SELECT session_id FROM transcripts")
            .expect("transcripts table missing");
        stmt.query_map([], |row| row.get::<_, String>(0))
            .unwrap()
            .filter_map(|r| r.ok())
            .collect()
    };
    drop(conn);

    eprintln!("Already tracked: {}", existing.len());

    // Parse in parallel
    let results: Mutex<Vec<SessionRecord>> = Mutex::new(Vec::new());
    let skipped = Mutex::new(0u64);

    chat_files.par_iter().for_each(|path| {
        let session_id = session_id_from_path(path);
        if existing.contains(&session_id) {
            *skipped.lock().unwrap() += 1;
            return;
        }

        if let Some(record) = parse_chat_file(path, &session_id) {
            results.lock().unwrap().push(record);
        }
    });

    let records = results.into_inner().unwrap();
    let skip_count = skipped.into_inner().unwrap();

    // Batch insert
    let mut conn = Connection::open(&db_path).expect("Failed to open tiramisu.db");
    let tx = conn.transaction().unwrap();
    for rec in &records {
        tx.execute(
            "INSERT OR IGNORE INTO transcripts (session_id, agent, cwd, title, message_count, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, datetime('now'))",
            params![
                rec.session_id,
                "kiro-ide",
                rec.workspace_hash,
                Option::<String>::None,
                rec.message_count,
            ],
        )
        .ok();
    }
    tx.commit().unwrap();

    let elapsed = start.elapsed();
    eprintln!(
        "\nDone in {:.2}s — New: {}, Skipped: {}, Human messages ingested: {}",
        elapsed.as_secs_f64(),
        records.len(),
        skip_count,
        records.iter().map(|r| r.human_count).sum::<i64>()
    );
}

fn session_id_from_path(path: &Path) -> String {
    // Use "ide:<workspace_hash>/<filename_stem>" as unique session ID
    let stem = path.file_stem().unwrap().to_string_lossy();
    let parent = path.parent().unwrap().file_name().unwrap().to_string_lossy();
    format!("ide:{}/{}", parent, stem)
}

fn parse_chat_file(path: &Path, session_id: &str) -> Option<SessionRecord> {
    let data = fs::read(path).ok()?;
    let chat: ChatFile = serde_json::from_slice(&data).ok()?;

    let messages = &chat.chat;
    if messages.is_empty() {
        return None;
    }

    let human_count = messages.iter().filter(|m| m.role == "human").count() as i64;
    let message_count = messages
        .iter()
        .filter(|m| m.role == "human" || m.role == "bot")
        .count() as i64;

    if human_count == 0 {
        return None; // Skip system-only sessions
    }

    let workspace_hash = path
        .parent()
        .unwrap()
        .file_name()
        .unwrap()
        .to_string_lossy()
        .to_string();

    Some(SessionRecord {
        session_id: session_id.to_string(),
        workspace_hash,
        message_count,
        human_count,
    })
}
