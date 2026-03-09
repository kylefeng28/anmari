use serde::Deserialize;
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Deserialize)]
pub struct Config {
    pub accounts: Vec<Account>,
}

#[derive(Debug, Deserialize)]
pub struct Account {
    pub email: String,
    pub imap_host: String,
    pub imap_port: u16,
    #[serde(default = "default_cache_days")]
    pub cache_days: u32,
    pub password: Option<String>,
}

fn default_cache_days() -> u32 {
    90
}

impl Config {
    pub fn load() -> Result<Self, Box<dyn std::error::Error>> {
        let config_path = get_config_path()?;
        let content = fs::read_to_string(&config_path)
            .map_err(|e| format!("Failed to read config at {:?}: {}", config_path, e))?;
        let config: Config = toml::from_str(&content)?;
        Ok(config)
    }

    pub fn get_account(&self, index: usize) -> Option<&Account> {
        self.accounts.get(index)
    }
}

fn get_config_path() -> Result<PathBuf, Box<dyn std::error::Error>> {
    let config_dir = dirs::config_dir().ok_or("Could not find config directory")?;
    Ok(config_dir.join("anmari/config.toml"))
}
