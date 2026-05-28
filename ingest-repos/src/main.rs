/// ingest-repos — shallow-clone trending GitHub repos and extract knowledge docs.
///
/// Reads a JSON array of repo objects from stdin (or a file arg):
///   [{"full_name":"owner/repo","stars":1000,"language":"Rust","description":"...","topics":[...]}]
///
/// For each repo:
///   1. git clone --depth 1
///   2. Walk files: collect *.md docs + source files (≤ 50KB, skip generated dirs)
///   3. Write a single knowledge .md file to OUTPUT_DIR
///   4. Delete the clone
///
/// Runs all clones in parallel via rayon.
use rayon::prelude::*;
use serde::Deserialize;
use std::collections::HashSet;
use std::env;
use std::fs;
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::process::Command;
use walkdir::WalkDir;

const MAX_FILE_BYTES: u64  = 50_000;
const MAX_SRC_FILES:  usize = 20;

#[derive(Deserialize, Clone)]
struct Repo {
    full_name:   String,
    stars:       Option<u64>,
    language:    Option<String>,
    description: Option<String>,
    #[serde(default)]
    topics:      Vec<String>,
}

fn skip_dir(name: &str) -> bool {
    matches!(
        name,
        "node_modules" | ".git" | "__pycache__" | "target" | "dist"
            | "build" | ".tox" | "vendor" | ".next" | "coverage"
    )
}

fn is_doc(path: &Path, root: &Path) -> bool {
    let name = path.file_name().unwrap_or_default().to_string_lossy().to_lowercase();
    if !name.ends_with(".md") {
        return false;
    }
    if name.starts_with("readme") {
        return true;
    }
    // markdown in doc dirs
    let doc_dirs: HashSet<&str> = ["docs","doc","documentation","wiki",".github","examples","example"]
        .iter().cloned().collect();
    path.strip_prefix(root).ok()
        .and_then(|rel| rel.components().next())
        .map(|c| doc_dirs.contains(c.as_os_str().to_string_lossy().to_lowercase().as_str()))
        .unwrap_or(false)
}

fn is_source(path: &Path) -> bool {
    matches!(
        path.extension().and_then(|e| e.to_str()).unwrap_or(""),
        "py" | "rs" | "ts" | "go" | "java" | "js" | "cpp" | "c" | "rb" | "swift" | "kt" | "cs"
    )
}

fn read_utf8(path: &Path) -> Option<String> {
    let bytes = fs::read(path).ok()?;
    Some(String::from_utf8_lossy(&bytes).into_owned())
}

fn collect_files(repo_dir: &Path) -> (Vec<PathBuf>, Vec<PathBuf>) {
    let mut docs = Vec::new();
    let mut srcs = Vec::new();

    for entry in WalkDir::new(repo_dir)
        .into_iter()
        .filter_entry(|e| {
            let name = e.file_name().to_string_lossy();
            !(e.file_type().is_dir() && skip_dir(&name))
        })
        .flatten()
    {
        if !entry.file_type().is_file() {
            continue;
        }
        let path = entry.path().to_owned();
        let Ok(meta) = path.metadata() else { continue };
        if meta.len() > MAX_FILE_BYTES {
            continue;
        }
        if is_doc(&path, repo_dir) {
            docs.push(path);
        } else if is_source(&path) && srcs.len() < MAX_SRC_FILES {
            srcs.push(path);
        }
    }
    (docs, srcs)
}

fn process_repo(repo: &Repo, output_dir: &Path, today: &str) -> Result<usize, String> {
    let full_name = &repo.full_name;
    let (owner, name) = full_name
        .split_once('/')
        .ok_or_else(|| format!("bad full_name: {full_name}"))?;

    let lang = repo.language.as_deref().unwrap_or("unknown");
    let stars = repo.stars.unwrap_or(0);
    let desc = repo.description.as_deref().unwrap_or("");
    let topics = repo.topics.join(", ");

    let tmp_dir = std::env::temp_dir().join(format!("tiramisu-repo-{name}"));
    // Clean up any leftover clone
    let _ = fs::remove_dir_all(&tmp_dir);

    let clone_status = Command::new("git")
        .args([
            "clone", "--depth", "1", "--single-branch", "--quiet",
            &format!("https://github.com/{full_name}"),
            tmp_dir.to_str().unwrap_or_default(),
        ])
        .status()
        .map_err(|e| format!("git not found: {e}"))?;

    if !clone_status.success() {
        return Err(format!("git clone failed for {full_name}"));
    }

    let (docs, srcs) = collect_files(&tmp_dir);

    let mut sections = vec![
        format!("# {full_name}"),
        format!(
            "<!-- category: reference-link | tags: github, trending, {} | promoted: {today} -->",
            lang.to_lowercase()
        ),
        String::new(),
        format!("**Stars:** {stars}  "),
        format!("**Language:** {lang}  "),
        format!("**Description:** {desc}  "),
        format!("**Topics:** {}  ", if topics.is_empty() { "none" } else { &topics }),
        format!("**URL:** https://github.com/{full_name}  "),
        String::new(),
        "---".into(),
        String::new(),
    ];

    for doc in &docs {
        if let Some(text) = read_utf8(doc) {
            let rel = doc.strip_prefix(&tmp_dir).unwrap_or(doc).display().to_string();
            sections.push(format!("## {rel}"));
            sections.push(String::new());
            sections.push(text.trim().to_string());
            sections.push(String::new());
        }
    }

    if !srcs.is_empty() {
        sections.push("## Source Files".into());
        sections.push(String::new());
        for src in &srcs {
            if let Some(text) = read_utf8(src) {
                let rel = src.strip_prefix(&tmp_dir).unwrap_or(src).display().to_string();
                let ext = src.extension().and_then(|e| e.to_str()).unwrap_or("");
                sections.push(format!("### `{rel}`"));
                sections.push(format!("```{ext}"));
                sections.push(text.trim().to_string());
                sections.push("```".into());
                sections.push(String::new());
            }
        }
    }

    let content = sections.join("\n");
    let byte_count = content.len();

    let out_file = output_dir.join(format!(
        "{}__{owner}__{name}.md",
        lang.to_lowercase()
    ));
    fs::write(&out_file, content.as_bytes())
        .map_err(|e| format!("write failed for {full_name}: {e}"))?;

    // Clean up clone
    let _ = fs::remove_dir_all(&tmp_dir);
    Ok(byte_count)
}

fn main() {
    let args: Vec<String> = env::args().collect();

    // Read JSON from file arg or stdin
    let json_str = if args.len() > 1 {
        fs::read_to_string(&args[1]).expect("failed to read input file")
    } else {
        let mut buf = String::new();
        io::stdin().read_to_string(&mut buf).expect("failed to read stdin");
        buf
    };

    let repos: Vec<Repo> = serde_json::from_str(&json_str).expect("invalid JSON input");

    let output_dir: PathBuf = if args.len() > 2 {
        PathBuf::from(&args[2])
    } else {
        let home = env::var("TIRAMISU_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                dirs_home().join(".tiramisu")
            });
        home.join("knowledge").join("github-trending")
    };

    fs::create_dir_all(&output_dir).expect("failed to create output dir");

    let today = {
        // Simple date: use env var or a fixed format via SystemTime
        use std::time::{SystemTime, UNIX_EPOCH};
        let secs = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let days = secs / 86400;
        // days since epoch → approximate YYYY-MM-DD
        epoch_days_to_date(days)
    };

    println!("[ingest-repos] {} repos to process in parallel", repos.len());

    let results: Vec<_> = repos
        .par_iter()
        .map(|repo| {
            let result = process_repo(repo, &output_dir, &today);
            (repo.full_name.clone(), result)
        })
        .collect();

    let mut ok = 0usize;
    let mut err = 0usize;
    for (name, result) in &results {
        match result {
            Ok(bytes) => {
                ok += 1;
                println!("[ingest-repos] OK {name} ({}KB)", bytes / 1024);
            }
            Err(e) => {
                err += 1;
                eprintln!("[ingest-repos] ERROR {name}: {e}");
            }
        }
    }

    println!("[ingest-repos] Done. OK: {ok} | Errors: {err}");
    println!("[ingest-repos] Output: {}", output_dir.display());
}

fn dirs_home() -> PathBuf {
    env::var("USERPROFILE")
        .or_else(|_| env::var("HOME"))
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
}

/// Convert days-since-Unix-epoch to YYYY-MM-DD string.
fn epoch_days_to_date(days: u64) -> String {
    let mut remaining = days as i64;
    let mut year = 1970i64;
    loop {
        let leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
        let days_in_year = if leap { 366 } else { 365 };
        if remaining < days_in_year {
            break;
        }
        remaining -= days_in_year;
        year += 1;
    }
    let leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
    let month_days: [i64; 12] = [
        31, if leap { 29 } else { 28 }, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31,
    ];
    let mut month = 1i64;
    for &md in &month_days {
        if remaining < md {
            break;
        }
        remaining -= md;
        month += 1;
    }
    format!("{year:04}-{month:02}-{:02}", remaining + 1)
}
