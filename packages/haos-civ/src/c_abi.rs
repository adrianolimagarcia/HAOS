use std::ffi::{CStr, CString};
use std::os::raw::c_char;

use crate::crypto::{compute_bundle_hash, compute_sha256};
use crate::leaf_protocol::build_temporary_soul;

#[no_mangle]
pub unsafe extern "C" fn haos_civ_sha256(input: *const c_char) -> *mut c_char {
    if input.is_null() {
        return std::ptr::null_mut();
    }
    let c_str = match CStr::from_ptr(input).to_str() {
        Ok(s) => s,
        Err(_) => return std::ptr::null_mut(),
    };
    let hash = compute_sha256(c_str);
    match CString::new(hash) {
        Ok(c) => c.into_raw(),
        Err(_) => std::ptr::null_mut(),
    }
}

#[no_mangle]
pub unsafe extern "C" fn haos_civ_bundle_hash(
    soul: *const c_char,
    identity: *const c_char,
    values: *const c_char,
) -> *mut c_char {
    let s = if soul.is_null() {
        ""
    } else {
        CStr::from_ptr(soul).to_str().unwrap_or("")
    };
    let i = if identity.is_null() {
        ""
    } else {
        CStr::from_ptr(identity).to_str().unwrap_or("")
    };
    let v = if values.is_null() {
        ""
    } else {
        CStr::from_ptr(values).to_str().unwrap_or("")
    };

    let hash = compute_bundle_hash(s, i, v);
    match CString::new(hash) {
        Ok(c) => c.into_raw(),
        Err(_) => std::ptr::null_mut(),
    }
}

#[no_mangle]
pub unsafe extern "C" fn haos_civ_temporary_soul(
    parent_soul: *const c_char,
    task_desc: *const c_char,
    constraints_json: *const c_char,
    council_context: *const c_char,
) -> *mut c_char {
    let s = if parent_soul.is_null() {
        ""
    } else {
        CStr::from_ptr(parent_soul).to_str().unwrap_or("")
    };
    let t = if task_desc.is_null() {
        ""
    } else {
        CStr::from_ptr(task_desc).to_str().unwrap_or("")
    };
    let c_json = if constraints_json.is_null() {
        ""
    } else {
        CStr::from_ptr(constraints_json).to_str().unwrap_or("")
    };
    let council = if council_context.is_null() {
        None
    } else {
        CStr::from_ptr(council_context).to_str().ok()
    };

    let constraints: Vec<String> = if !c_json.is_empty() {
        serde_json::from_str(c_json).unwrap_or_default()
    } else {
        Vec::new()
    };

    let temp_soul = build_temporary_soul(s, t, &constraints, council);
    match CString::new(temp_soul) {
        Ok(c) => c.into_raw(),
        Err(_) => std::ptr::null_mut(),
    }
}

#[no_mangle]
pub unsafe extern "C" fn haos_civ_free_string(ptr: *mut c_char) {
    if !ptr.is_null() {
        drop(CString::from_raw(ptr));
    }
}
