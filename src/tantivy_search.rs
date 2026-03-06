use tantivy::schema::*;
use tantivy::{doc, Index, IndexWriter, ReloadPolicy};
use tantivy::collector::TopDocs;
use tantivy::query::QueryParser;
use std::path::PathBuf;

pub struct SearchIndex {
    index: Index,
    writer: IndexWriter,
    schema: Schema,
    uid_field: Field,
    folder_field: Field,
    from_addr_field: Field,
    from_name_field: Field,
    subject_field: Field,
    body_field: Field,
    date_field: Field,
}

impl SearchIndex {
    pub fn new(account_index: usize) -> Result<Self, Box<dyn std::error::Error>> {
        let index_path = Self::get_index_path(account_index)?;
        std::fs::create_dir_all(&index_path)?;

        let mut schema_builder = Schema::builder();
        let uid_field = schema_builder.add_u64_field("uid", STORED | INDEXED);
        let folder_field = schema_builder.add_text_field("folder", STRING | STORED);
        let from_addr_field = schema_builder.add_text_field("from_addr", TEXT | STORED);
        let from_name_field = schema_builder.add_text_field("from_name", TEXT | STORED);
        let subject_field = schema_builder.add_text_field("subject", TEXT | STORED);
        let body_field = schema_builder.add_text_field("body", TEXT);
        let date_field = schema_builder.add_text_field("date", STRING | STORED);
        let schema = schema_builder.build();

        let index = Index::open_or_create(tantivy::directory::MmapDirectory::open(&index_path)?, schema.clone())?;
        let writer = index.writer(50_000_000)?; // 50MB buffer

        Ok(Self {
            index,
            writer,
            schema,
            uid_field,
            folder_field,
            from_addr_field,
            from_name_field,
            subject_field,
            body_field,
            date_field,
        })
    }

    fn get_index_path(account_index: usize) -> Result<PathBuf, Box<dyn std::error::Error>> {
        let state_dir = dirs::state_dir()
            .or(dirs::home_dir().map(|x| x.join(".local/state")))
            .ok_or("Could not find state directory")?;
        Ok(state_dir.join("anmari").join(format!("index_{}", account_index)))
    }

    pub fn index_message(
        &mut self,
        uid: u32,
        folder: &str,
        from_addr: &str,
        from_name: Option<&str>,
        subject: &str,
        date: &str,
        body: Option<&str>,
    ) -> Result<(), Box<dyn std::error::Error>> {
        // Delete existing document first (for updates)
        let term = Term::from_field_u64(self.uid_field, uid as u64);
        self.writer.delete_term(term);

        // Add new document
        let mut doc = doc!(
            self.uid_field => uid as u64,
            self.folder_field => folder,
            self.from_addr_field => from_addr,
            self.subject_field => subject,
            self.date_field => date,
        );

        if let Some(name) = from_name {
            doc.add_text(self.from_name_field, name);
        }

        if let Some(body_text) = body {
            doc.add_text(self.body_field, body_text);
        }

        self.writer.add_document(doc)?;
        Ok(())
    }

    pub fn delete_message(&mut self, uid: u32) -> Result<(), Box<dyn std::error::Error>> {
        let term = Term::from_field_u64(self.uid_field, uid as u64);
        self.writer.delete_term(term);
        Ok(())
    }

    pub fn commit(&mut self) -> Result<(), Box<dyn std::error::Error>> {
        self.writer.commit()?;
        Ok(())
    }

    pub fn search(&self, query_str: &str, limit: usize) -> Result<Vec<SearchResult>, Box<dyn std::error::Error>> {
        let reader = self.index
            .reader_builder()
            .reload_policy(ReloadPolicy::OnCommitWithDelay)
            .try_into()?;
        let searcher = reader.searcher();

        let query_parser = QueryParser::for_index(
            &self.index,
            vec![
                self.from_addr_field,
                self.from_name_field,
                self.subject_field,
                self.body_field,
            ],
        );

        let query = query_parser.parse_query(query_str)?;
        let top_docs = searcher.search(&query, &TopDocs::with_limit(limit))?;

        let mut results = Vec::new();
        for (_score, doc_address) in top_docs {
            let doc: tantivy::TantivyDocument = searcher.doc(doc_address)?;
            let uid = doc.get_first(self.uid_field)
                .and_then(|v: &tantivy::schema::OwnedValue| v.as_u64())
                .unwrap_or(0) as u32;
            let folder = doc.get_first(self.folder_field)
                .and_then(|v: &tantivy::schema::OwnedValue| v.as_str())
                .unwrap_or("")
                .to_string();
            let from_addr = doc.get_first(self.from_addr_field)
                .and_then(|v: &tantivy::schema::OwnedValue| v.as_str())
                .unwrap_or("")
                .to_string();
            let subject = doc.get_first(self.subject_field)
                .and_then(|v: &tantivy::schema::OwnedValue| v.as_str())
                .unwrap_or("")
                .to_string();
            let date = doc.get_first(self.date_field)
                .and_then(|v: &tantivy::schema::OwnedValue| v.as_str())
                .unwrap_or("")
                .to_string();

            results.push(SearchResult {
                uid,
                folder,
                from_addr,
                subject,
                date,
            });
        }

        Ok(results)
    }

    /// Get UIDs of messages that don't have body text indexed
    /// This checks if the body field is empty/missing for each document
    pub fn get_uids_without_bodies(&self, _folder: &str, all_uids: &[u32]) -> Result<Vec<u32>, Box<dyn std::error::Error>> {
        let reader = self.index
            .reader_builder()
            .reload_policy(ReloadPolicy::OnCommitWithDelay)
            .try_into()?;
        let searcher = reader.searcher();

        let mut missing = Vec::new();

        for &uid in all_uids {
            // Search for this specific UID
            let uid_term = Term::from_field_u64(self.uid_field, uid as u64);
            let uid_query = tantivy::query::TermQuery::new(uid_term, tantivy::schema::IndexRecordOption::Basic);

            let top_docs = searcher.search(&uid_query, &TopDocs::with_limit(1))?;

            if let Some((_score, doc_address)) = top_docs.first() {
                let doc: tantivy::TantivyDocument = searcher.doc(*doc_address)?;

                // Check if body field exists and has content
                let has_body = doc.get_first(self.body_field).is_some();

                if !has_body {
                    missing.push(uid);
                }
            } else {
                // Document not in index at all
                missing.push(uid);
            }
        }

        Ok(missing)
    }
}

#[derive(Debug)]
pub struct SearchResult {
    pub uid: u32,
    pub folder: String,
    pub from_addr: String,
    pub subject: String,
    pub date: String,
}
