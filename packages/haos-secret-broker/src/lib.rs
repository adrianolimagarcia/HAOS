use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

pub const MAX_FRAME: usize = 64 * 1024;

#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum Request { Get { profile: String, name: String }, Set { profile: String, name: String, value: String }, Delete { profile: String, name: String } }
#[derive(Debug, Serialize, Deserialize, PartialEq)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum Response { Ok { value: Option<String> }, Error { code: String, message: String } }

#[derive(Debug, thiserror::Error)]
pub enum Error { #[error("invalid request")] Invalid, #[error("frame too large")] TooLarge, #[error("unauthorized")] Unauthorized, #[error("backend error")] Backend }

pub trait Authorizer: Send + Sync { fn authorize(&self, profile: &str, op: &str, name: &str) -> bool; }

#[derive(Clone, Default)]
pub struct InMemoryBackend { data: Arc<Mutex<HashMap<(String,String), String>>> }
impl InMemoryBackend { pub fn new() -> Self { Self::default() } }

pub struct Broker<A: Authorizer> { backend: InMemoryBackend, authorizer: A }
impl<A: Authorizer> Broker<A> {
 pub fn new(backend: InMemoryBackend, authorizer: A) -> Self { Self { backend, authorizer } }
 pub fn handle(&self, req: Request) -> Response {
  let (profile, op, name) = match &req { Request::Get{profile,name} => (profile,"get",name), Request::Set{profile,name,..} => (profile,"set",name), Request::Delete{profile,name} => (profile,"delete",name) };
  if !self.authorizer.authorize(profile,op,name) { return Response::Error{code:"unauthorized".into(),message:"request denied".into()}; }
  let mut d=self.backend.data.lock().map_err(|_| ()).ok(); if d.is_none(){return Response::Error{code:"backend".into(),message:"backend unavailable".into()};} let d=d.as_mut().unwrap();
  match req { Request::Get{profile,name} => Response::Ok{value:d.get(&(profile,name)).cloned()}, Request::Set{profile,name,value} => {d.insert((profile,name),value); Response::Ok{value:None}}, Request::Delete{profile,name} => {d.remove(&(profile,name)); Response::Ok{value:None}} }
 }
}

pub fn encode(req: &Request) -> Result<Vec<u8>, Error> { let body=serde_json::to_vec(req).map_err(|_|Error::Invalid)?; if body.len()>MAX_FRAME{return Err(Error::TooLarge)}; let mut out=(body.len() as u32).to_be_bytes().to_vec(); out.extend(body); Ok(out) }
pub fn decode(frame: &[u8]) -> Result<Request, Error> { if frame.len()<4{return Err(Error::Invalid)}; let n=u32::from_be_bytes(frame[..4].try_into().unwrap()) as usize; if n>MAX_FRAME || n!=frame.len()-4{return Err(Error::Invalid)}; serde_json::from_slice(&frame[4..]).map_err(|_|Error::Invalid) }

#[cfg(test)] mod tests { use super::*; struct Allow; impl Authorizer for Allow {fn authorize(&self,_:&str,_:&str,_:&str)->bool{true}} #[test] fn roundtrip(){let r=Request::Get{profile:"p".into(),name:"n".into()};assert!(matches!(decode(&encode(&r).unwrap()),Ok(Request::Get{..})));} #[test] fn scoped(){let b=Broker::new(InMemoryBackend::new(),Allow); b.handle(Request::Set{profile:"a".into(),name:"x".into(),value:"v".into()}); assert_eq!(b.handle(Request::Get{profile:"b".into(),name:"x".into()}),Response::Ok{value:None});} }
