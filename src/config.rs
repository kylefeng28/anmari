use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AccountConfig {
    pub email: String,
    pub imap_host: String,
    pub imap_port: u16,
    #[serde(default = "default_cache_days")]
    pub cache_days: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub password: Option<String>,
}

fn default_cache_days() -> u32 {
    90
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Config {
    pub accounts: Vec<AccountConfig>,
}

impl Config {
    pub fn load() -> Result<Self> {
        let path = Self::config_path()?;
        
        if !path.exists() {
            return Ok(Self {
                accounts: Vec::new(),
            });
        }
        
        let content = fs::read_to_string(&path)
            .context("Failed to read config file")?;
        
        toml::from_str(&content)
            .context("Failed to parse config file")
    }
    
    pub fn save(&self) -> Result<()> {
        let path = Self::config_path()?;
        
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        
        let content = toml::to_string_pretty(self)?;
        fs::write(&path, content)?;
        
        Ok(())
    }
    
    pub fn config_path() -> Result<PathBuf> {
        let config_dir = dirs::config_dir()
            .context("Could not find config directory")?;
        Ok(config_dir.join("anmari").join("config.toml"))
    }
}

