use pyo3::prelude::*;
use pyo3::types::{PyAny, PyString};
use pyo3::exceptions::PyRuntimeError;
use crate::cache::{EmailCache, CachedMessage, CachedFolderState};
use std::path::PathBuf;
use rusqlite::Transaction;
use core::ffi::c_void;

macro_rules! py_result {
    ($expr:expr) => {
        $expr.map_err(|e| PyRuntimeError::new_err(e.to_string()))
    };
}

macro_rules! forward_method {
    ($self:ident, $method:ident($($arg:expr),*)) => {
        py_result!($self.cache.$method($($arg),*))
    };

    ($self:ident, $method:ident($($arg:expr),*), $auto_commit:expr) => {{
        $self._start_transaction_auto_commit($auto_commit)?;
        forward_method!($self, $method($($arg),*))

    }};
}

#[pyclass(unsendable)]
struct PyEmailCache {
    cache: EmailCache,
    tx_ptr: Option<*const c_void>
}

#[pyclass]
#[derive(Clone)]
struct PyCachedMessage {
    #[pyo3(get)]
    uid: u32,
    #[pyo3(get)]
    folder: String,
    #[pyo3(get)]
    from_addr: String,
    #[pyo3(get)]
    from_name: Option<String>,
    #[pyo3(get)]
    subject: String,
    #[pyo3(get)]
    date: String,
    #[pyo3(get)]
    flags: String,
}

impl From<CachedMessage> for PyCachedMessage {
    fn from(msg: CachedMessage) -> Self {
        PyCachedMessage {
            uid: msg.uid,
            folder: msg.folder,
            from_addr: msg.from_addr,
            from_name: msg.from_name,
            subject: msg.subject,
            date: msg.date,
            flags: msg.flags,
        }
    }
}

impl From<PyCachedMessage> for CachedMessage {
    fn from(msg: PyCachedMessage) -> Self {
        CachedMessage {
            uid: msg.uid,
            folder: msg.folder,
            from_addr: msg.from_addr,
            from_name: msg.from_name,
            subject: msg.subject,
            date: msg.date,
            flags: msg.flags,
        }
    }
}

#[pymethods]
impl PyCachedMessage {
    fn get_flags_as_list(&self) -> Vec<String> {
        if self.flags.is_empty() {
            Vec::new()
        } else {
            self.flags.split(' ').map(|s| s.to_string()).collect()
        }
    }
}

#[pymethods]
impl PyEmailCache {
    #[new]
    #[pyo3(signature = (account_index, _cache_days=None))]
    fn new(account_index: usize, _cache_days: Option<i32>) -> PyResult<Self> {
        Ok(PyEmailCache { cache: py_result!(EmailCache::new(account_index))?, tx_ptr: None })
    }

    #[staticmethod]
    fn from_path(db_path: PathBuf, _cache_days: Option<i32>) -> PyResult<Self> {
        Ok(PyEmailCache { cache: py_result!(EmailCache::new_from_path(db_path))?, tx_ptr: None })
    }

    fn _start_transaction(&mut self) -> PyResult<()> {
        let tx = py_result!(self.cache.transaction())?;
        self.tx_ptr = Some(Box::into_raw(Box::new(tx)) as *const c_void);
        Ok(())
    }

    fn _start_transaction_auto_commit(&mut self, auto_commit: bool) -> PyResult<()> {
        Ok(match self.tx_ptr {
            Some(_) => self._start_transaction()?,
            None => (),
        })
    }

    fn commit(&mut self) -> PyResult<()> {
        if let Some(tx_ptr) = self.tx_ptr.take() {
            let raw_ptr = tx_ptr as *mut Transaction;
            let tx_box = unsafe {
                Box::from_raw(raw_ptr)
            };
            py_result!(tx_box.commit())?;
        }
        else {
        }
        Ok(())
    }

    fn get_message(&self, uid: u32, folder: &str) -> PyResult<Option<PyCachedMessage>> {
        forward_method!(self, get_message(uid, folder)).map(|opt| opt.map(Into::into))
    }

    #[pyo3(signature = (uid, folder, from_addr, from_name, subject, date, flags, commit = true))]
    fn insert_message(&mut self, uid: u32, folder: &str, from_addr: &str, from_name: Option<&str>, 
                      subject: &str, date: &Bound<'_, PyAny>, flags: Vec<String>, commit: bool) -> PyResult<()> {
        let date_str = date.call_method0("__str__")?;
        let date_str: &str = date_str.extract()?;
        forward_method!(self, insert_message(uid, folder, from_addr, from_name, subject, date_str, &flags), commit)
    }

    #[pyo3(signature = (uid, folder, flags, commit = true))]
    fn update_flags(&mut self, uid: u32, folder: &str, flags: Vec<String>, commit: bool) -> PyResult<()> {
        forward_method!(self, update_message_flags(uid, folder, &flags), commit)
    }

    #[pyo3(signature = (uid, folder, commit = true))]
    fn delete_message(&mut self, uid: u32, folder: &str, commit: bool) -> PyResult<()> {
        forward_method!(self, delete_message(uid, folder), commit)
    }

    #[pyo3(signature = (source_uid, source_folder, dest_uid, dest_folder, commit = true))]
    fn copy_message(&mut self, source_uid: u32, source_folder: &str, dest_uid: u32, dest_folder: &str, commit: bool) -> PyResult<()> {
        forward_method!(self, copy_message(source_uid, source_folder, dest_uid, dest_folder), commit)
    }

    fn get_last_seen_uid(&self, folder: &str) -> PyResult<Option<u32>> {
        forward_method!(self, get_last_seen_uid(folder))
    }

    fn get_all_uids(&self, folder: &str) -> PyResult<Vec<u32>> {
        forward_method!(self, get_all_uids(folder)).map(|uids| uids.into_iter().map(|u| u.get()).collect())
    }

    fn search(&self, folder: &str, query: &str) -> PyResult<Vec<PyCachedMessage>> {
        forward_method!(self, search(folder, query)).map(|msgs| msgs.into_iter().map(Into::into).collect())
    }

    fn get_folder_state(&self, folder: &str) -> PyResult<Option<(u32, u64)>> {
        forward_method!(self, get_folder_state(folder)).map(|opt| opt.map(|state| (state.uidvalidity, state.highestmodseq)))
        // forward_method!(self, get_folder_state(folder)).map(|x| x.map(|state| (state.uidvalidity, state.highestmodseq)))
    }

    fn set_folder_state(&self, folder: &str, uidvalidity: u32, highestmodseq: u64) -> PyResult<()> {
        forward_method!(self, set_folder_state(folder, uidvalidity, highestmodseq))
    }

    fn clear_folders_state_for_cache_cleanup(&self, folders: Vec<String>) -> PyResult<()> {
        forward_method!(self, clear_folders_state_for_cache_cleanup(&folders))
    }

    fn clear_folder_messages_for_uidvalidity_change(&self, folder: &str) -> PyResult<()> {
        forward_method!(self, clear_folder_messages_for_uidvalidity_change(folder))
    }

    #[pyo3(signature = (uid, folder, tag, commit = true))]
    fn add_tag(&mut self, uid: u32, folder: &str, tag: &str, commit: bool) -> PyResult<()> {
        forward_method!(self, add_tag(uid, folder, tag), commit)
    }

    #[pyo3(signature = (uid, folder, tag, commit = true))]
    fn remove_tag(&mut self, uid: u32, folder: &str, tag: &str, commit: bool) -> PyResult<()> {
        forward_method!(self, remove_tag(uid, folder, tag), commit)
    }

    fn get_tags(&self, uid: u32, folder: &str) -> PyResult<Vec<String>> {
        forward_method!(self, get_tags(uid, folder))
    }

    fn tag_messages(&self, messages: Vec<PyCachedMessage>, tags_to_add: Vec<String>, tags_to_remove: Vec<String>) -> PyResult<usize> {
        let msgs: Vec<CachedMessage> = messages.into_iter().map(Into::into).collect();
        forward_method!(self, tag_messages(&msgs, &tags_to_add, &tags_to_remove))
    }

    #[pyo3(signature = (uid, folder, labels, commit = true))]
    fn set_gm_labels(&mut self, uid: u32, folder: &str, labels: Vec<String>, commit: bool) -> PyResult<()> {
        forward_method!(self, set_gm_labels(uid, folder, &labels), commit)
    }

    fn get_gm_labels(&self, uid: u32, folder: &str) -> PyResult<Vec<String>> {
        forward_method!(self, get_gm_labels(uid, folder))
    }
}

#[pymodule]
fn anmari_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyEmailCache>()?;
    m.add_class::<PyCachedMessage>()?;
    Ok(())
}
