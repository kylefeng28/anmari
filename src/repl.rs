use rustyline::{DefaultEditor, error::ReadlineError};
use std::path::PathBuf;

pub fn run_repl() {
    let history_path = dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".anmari_history");

    let mut rl = DefaultEditor::new().expect("Failed to create editor");
    let _ = rl.load_history(&history_path);

    println!("anmari interactive mode");
    println!("Type 'help' for commands, 'exit' or Ctrl+D to quit\n");

    loop {
        match rl.readline("anmari> ") {
            Ok(line) => {
                let line = line.trim().to_string();
                if line.is_empty() {
                    continue;
                }

                let _ = rl.add_history_entry(&line);

                if matches!(line.as_str(), "exit" | "quit") {
                    println!("Goodbye!");
                    break;
                }

                match shell_words::split(&line) {
                    Ok(argv) if !argv.is_empty() => {
                        if let Err(e) = crate::run_command(argv) {
                            eprintln!("Error: {}", e);
                        }
                    }
                    Ok(_) => {}
                    Err(e) => eprintln!("Error parsing command: {}", e),
                }
            }
            Err(ReadlineError::Interrupted) => {
                println!("(Use 'exit' or Ctrl+D to quit)");
            }
            Err(ReadlineError::Eof) => {
                println!("\nGoodbye!");
                break;
            }
            Err(e) => {
                eprintln!("Error: {}", e);
                break;
            }
        }
    }

    let _ = rl.save_history(&history_path);
}
