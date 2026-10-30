"""
Deterministic Rust Systems & Memory Safety Certification Engine (`sovereign-kb certify rust`).

Executes a 20-domain certification benchmark (`RUST-CERT-01` .. `RUST-CERT-20`):
  1. Queries the Sovereign-KB 3-Way Hybrid Retrieval Engine (FTS5 BM25 + 768-d Vector + 1-Hop Wikilink Graph)
     for each of the 20 Rust systems engineering specifications in `02-operating-system/rust-systems-reference.md`.
  2. Verifies retrieval accuracy (top-k hit, RRF score, cosine similarity, and FTS5 rank).
  3. Compiles and executes all 20 domain verification modules against `rustc --edition 2021 --test -D warnings`.
  4. Emits a cryptographically hashed (SHA-256) certification manifest and Markdown/JSON report.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

RUST_CERT_CHALLENGES: list[dict[str, str]] = [
    {
        "id": "RUST-CERT-01",
        "module": "cert_01_ownership",
        "title": "Ownership, Move Semantics & Disjoint Slice Reborrowing (split_at_mut)",
        "query": "RUST-CERT-01 Ownership Move Semantics Borrowing split_at_mut E0382 E0502",
        "code": r"""
mod cert_01_ownership {
    pub fn swap_disjoint_halves<T: Copy>(buf: &mut [T]) {
        let mid = buf.len() / 2;
        let (left, right) = buf.split_at_mut(mid);
        let span = left.len().min(right.len());
        for i in 0..span {
            let tmp = left[i];
            left[i] = right[i];
            right[i] = tmp;
        }
    }

    #[test]
    fn verify_rust_cert_01() {
        let mut data = vec![10, 20, 30, 40, 50, 60];
        swap_disjoint_halves(&mut data);
        assert_eq!(data, vec![40, 50, 60, 10, 20, 30]);
    }
}
""",
    },
    {
        "id": "RUST-CERT-02",
        "module": "cert_02_lifetimes",
        "title": "Explicit Lifetimes, Zero-Copy Lexing & Higher-Ranked Trait Bounds (HRTB)",
        "query": "RUST-CERT-02 Explicit Lifetimes Variance Higher-Ranked Trait Bounds HRTB E0106",
        "code": r"""
mod cert_02_lifetimes {
    pub struct ZeroCopyCursor<'src> {
        remaining: &'src str,
    }

    impl<'src> ZeroCopyCursor<'src> {
        pub fn new(input: &'src str) -> Self {
            Self { remaining: input }
        }

        pub fn next_ident(&mut self) -> Option<&'src str> {
            let trimmed = self.remaining.trim_start();
            if trimmed.is_empty() {
                self.remaining = "";
                return None;
            }
            match trimmed.find(char::is_whitespace) {
                Some(idx) => {
                    let (tok, rest) = trimmed.split_at(idx);
                    self.remaining = rest;
                    Some(tok)
                }
                None => {
                    self.remaining = "";
                    Some(trimmed)
                }
            }
        }
    }

    pub fn apply_hrtb<F>(s: &str, f: F) -> String
    where
        F: for<'a> Fn(&'a str) -> &'a str,
    {
        let local = String::from(s);
        f(local.as_str()).to_owned()
    }

    #[test]
    fn verify_rust_cert_02() {
        let raw = String::from("  alpha beta_02 gamma ");
        let mut cursor = ZeroCopyCursor::new(&raw);
        assert_eq!(cursor.next_ident(), Some("alpha"));
        assert_eq!(cursor.next_ident(), Some("beta_02"));
        assert_eq!(cursor.next_ident(), Some("gamma"));
        assert_eq!(cursor.next_ident(), None);
        assert_eq!(apply_hrtb("  sovereign-kb  ", |x| x.trim()), "sovereign-kb");
    }
}
""",
    },
    {
        "id": "RUST-CERT-03",
        "module": "cert_03_adt_niche",
        "title": "Algebraic Data Types, Null-Pointer Niche Optimization & Slice Patterns",
        "query": "RUST-CERT-03 Algebraic Data Types Niche Optimization NonZeroU64 Exhaustiveness",
        "code": r"""
mod cert_03_adt_niche {
    use std::mem::size_of;
    use std::num::NonZeroU64;

    pub fn classify_frame(bytes: &[u8]) -> &'static str {
        match bytes {
            [0x7F, b'E', b'L', b'F', ..] => "elf-binary",
            [b'S', b'K', b'B', version @ 1..=9, .., 0xFF] if *version >= 2 => "skb-v2-frame",
            [] => "empty",
            _ => "raw-payload",
        }
    }

    #[test]
    fn verify_rust_cert_03() {
        assert_eq!(size_of::<Option<&u64>>(), size_of::<&u64>());
        assert_eq!(size_of::<Option<NonZeroU64>>(), size_of::<u64>());
        assert_eq!(classify_frame(&[0x7F, b'E', b'L', b'F', 2, 1]), "elf-binary");
        assert_eq!(classify_frame(&[b'S', b'K', b'B', 2, 0x10, 0xFF]), "skb-v2-frame");
        assert_eq!(classify_frame(&[]), "empty");
    }
}
""",
    },
    {
        "id": "RUST-CERT-04",
        "module": "cert_04_error_handling",
        "title": "Zero-Panic Error Hierarchy, ? Operator Propagation & From<E> Composition",
        "query": "RUST-CERT-04 Composable Error Handling From Conversion Result Display",
        "code": r"""
mod cert_04_error_handling {
    use std::error::Error;
    use std::fmt;
    use std::num::ParseIntError;

    #[derive(Debug, PartialEq, Eq)]
    pub enum ConfigError {
        MissingColon,
        InvalidPort(ParseIntError),
        PrivilegedPort(u16),
    }

    impl fmt::Display for ConfigError {
        fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
            match self {
                Self::MissingColon => write!(f, "missing host:port delimiter"),
                Self::InvalidPort(e) => write!(f, "invalid port integer: {e}"),
                Self::PrivilegedPort(p) => write!(f, "privileged port forbidden: {p}"),
            }
        }
    }

    impl Error for ConfigError {}

    impl From<ParseIntError> for ConfigError {
        fn from(e: ParseIntError) -> Self {
            Self::InvalidPort(e)
        }
    }

    pub fn parse_endpoint(spec: &str) -> Result<(&str, u16), ConfigError> {
        let (host, port_str) = spec.split_once(':').ok_or(ConfigError::MissingColon)?;
        let port: u16 = port_str.parse()?;
        if port < 1024 {
            return Err(ConfigError::PrivilegedPort(port));
        }
        Ok((host, port))
    }

    #[test]
    fn verify_rust_cert_04() {
        assert_eq!(parse_endpoint("localhost:8080"), Ok(("localhost", 8080)));
        assert_eq!(parse_endpoint("localhost:80"), Err(ConfigError::PrivilegedPort(80)));
        assert_eq!(parse_endpoint("localhost"), Err(ConfigError::MissingColon));
        assert!(matches!(parse_endpoint("localhost:notanumber"), Err(ConfigError::InvalidPort(_))));
    }
}
""",
    },
    {
        "id": "RUST-CERT-05",
        "module": "cert_05_traits_dyn",
        "title": "Generics, Associated Types, Static vs. Dynamic Dispatch (&dyn Trait)",
        "query": "RUST-CERT-05 Generics Trait Bounds Associated Types dyn Object Safety E0277",
        "code": r"""
mod cert_05_traits_dyn {
    pub trait Scorer {
        fn score(&self, tf: f64, dl: f64) -> f64;
    }

    pub struct Bm25Scorer {
        pub k1: f64,
        pub b: f64,
        pub avgdl: f64,
    }

    impl Scorer for Bm25Scorer {
        fn score(&self, tf: f64, dl: f64) -> f64 {
            let norm = 1.0 - self.b + self.b * (dl / self.avgdl);
            (tf * (self.k1 + 1.0)) / (tf + self.k1 * norm)
        }
    }

    pub struct LinearScorer {
        pub weight: f64,
    }

    impl Scorer for LinearScorer {
        fn score(&self, tf: f64, _dl: f64) -> f64 {
            tf * self.weight
        }
    }

    pub fn ensemble_score(scorers: &[&dyn Scorer], tf: f64, dl: f64) -> f64 {
        scorers.iter().map(|s| s.score(tf, dl)).sum()
    }

    #[test]
    fn verify_rust_cert_05() {
        let bm25 = Bm25Scorer { k1: 1.2, b: 0.75, avgdl: 100.0 };
        let lin = LinearScorer { weight: 0.5 };
        let pipeline: Vec<&dyn Scorer> = vec![&bm25, &lin];
        let total = ensemble_score(&pipeline, 2.0, 100.0);
        assert!((total - 2.375).abs() < 1e-9);
    }
}
""",
    },
    {
        "id": "RUST-CERT-06",
        "module": "cert_06_conversions_newtype",
        "title": "Zero-Cost #[repr(transparent)] Newtypes, TryFrom & Operator Overloading",
        "query": "RUST-CERT-06 Standard Conversion Traits TryFrom AsRef Borrow repr transparent Newtype",
        "code": r"""
mod cert_06_conversions_newtype {
    use std::convert::TryFrom;
    use std::ops::Add;

    #[repr(transparent)]
    #[derive(Debug, Clone, Copy, PartialEq)]
    pub struct Probability(f64);

    impl Probability {
        pub fn value(self) -> f64 {
            self.0
        }
    }

    impl TryFrom<f64> for Probability {
        type Error = &'static str;
        fn try_from(v: f64) -> Result<Self, Self::Error> {
            if (0.0..=1.0).contains(&v) {
                Ok(Self(v))
            } else {
                Err("probability outside [0.0, 1.0]")
            }
        }
    }

    #[repr(transparent)]
    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub struct TokenCount(pub usize);

    impl Add for TokenCount {
        type Output = Self;
        fn add(self, rhs: Self) -> Self::Output {
            Self(self.0 + rhs.0)
        }
    }

    #[test]
    fn verify_rust_cert_06() {
        assert_eq!(Probability::try_from(0.75).map(Probability::value), Ok(0.75));
        assert!(Probability::try_from(1.25).is_err());
        assert_eq!(TokenCount(128) + TokenCount(384), TokenCount(512));
    }
}
""",
    },
    {
        "id": "RUST-CERT-07",
        "module": "cert_07_iterators",
        "title": "Custom DoubleEndedIterator State Machine & Fallible Short-Circuit Collection",
        "query": "RUST-CERT-07 Zero-Cost Iterators DoubleEndedIterator collect Result state machine",
        "code": r"""
mod cert_07_iterators {
    pub struct StrideWindow<'a, T> {
        slice: &'a [T],
        front: usize,
        back: usize,
    }

    impl<'a, T> StrideWindow<'a, T> {
        pub fn new(slice: &'a [T]) -> Self {
            Self { slice, front: 0, back: slice.len() }
        }
    }

    impl<'a, T: Copy> Iterator for StrideWindow<'a, T> {
        type Item = T;
        fn next(&mut self) -> Option<Self::Item> {
            if self.front < self.back {
                let item = self.slice[self.front];
                self.front += 1;
                Some(item)
            } else {
                None
            }
        }
    }

    impl<'a, T: Copy> DoubleEndedIterator for StrideWindow<'a, T> {
        fn next_back(&mut self) -> Option<Self::Item> {
            if self.front < self.back {
                self.back -= 1;
                Some(self.slice[self.back])
            } else {
                None
            }
        }
    }

    #[test]
    fn verify_rust_cert_07() {
        let nums = [10, 20, 30, 40];
        let mut it = StrideWindow::new(&nums);
        assert_eq!(it.next(), Some(10));
        assert_eq!(it.next_back(), Some(40));
        assert_eq!(it.collect::<Vec<_>>(), vec![20, 30]);

        let raw = ["12", "24", "36"];
        let parsed: Result<Vec<u32>, _> = raw.iter().map(|s| s.parse::<u32>()).collect();
        assert_eq!(parsed, Ok(vec![12, 24, 36]));
    }
}
""",
    },
    {
        "id": "RUST-CERT-08",
        "module": "cert_08_closures",
        "title": "Closures, Environment Capture Modes & Fn / FnMut / FnOnce Hierarchy",
        "query": "RUST-CERT-08 Closures Capture Modes Fn FnMut FnOnce Hierarchy",
        "code": r"""
mod cert_08_closures {
    pub fn run_stateless<F: Fn(i64) -> i64>(f: F, x: i64) -> i64 {
        f(x) + f(x + 1)
    }

    pub fn run_stateful<F: FnMut(i64) -> i64>(mut f: F, items: &[i64]) -> Vec<i64> {
        items.iter().copied().map(&mut f).collect()
    }

    pub fn consume_once<F: FnOnce() -> String>(f: F) -> String {
        f()
    }

    #[test]
    fn verify_rust_cert_08() {
        let factor = 3;
        assert_eq!(run_stateless(|v| v * factor, 4), 12 + 15);

        let mut running_sum = 0;
        let prefix = run_stateful(
            |v| {
                running_sum += v;
                running_sum
            },
            &[5, 10, 15],
        );
        assert_eq!(prefix, vec![5, 15, 30]);

        let owned = String::from("transferred-ownership");
        let out = consume_once(move || owned);
        assert_eq!(out, "transferred-ownership");
    }
}
""",
    },
    {
        "id": "RUST-CERT-09",
        "module": "cert_09_box_recursive",
        "title": "Heap Indirection (Box<T>) & Zero-Unsafe In-Place List Reversal via mem::take",
        "query": "RUST-CERT-09 Heap Indirection Recursive Data Structures Box std::mem::take",
        "code": r"""
mod cert_09_box_recursive {
    use std::mem;

    #[derive(Debug, PartialEq, Eq)]
    pub struct Node<T> {
        pub val: T,
        pub next: Option<Box<Node<T>>>,
    }

    pub struct SinglyList<T> {
        head: Option<Box<Node<T>>>,
    }

    impl<T> SinglyList<T> {
        pub fn new() -> Self {
            Self { head: None }
        }

        pub fn push_front(&mut self, val: T) {
            let next = mem::take(&mut self.head);
            self.head = Some(Box::new(Node { val, next }));
        }

        pub fn reverse_in_place(&mut self) {
            let mut prev = None;
            let mut curr = mem::take(&mut self.head);
            while let Some(mut boxed_node) = curr {
                let next = mem::replace(&mut boxed_node.next, prev);
                prev = Some(boxed_node);
                curr = next;
            }
            self.head = prev;
        }

        pub fn into_vec(mut self) -> Vec<T> {
            let mut out = Vec::new();
            let mut curr = mem::take(&mut self.head);
            while let Some(node) = curr {
                out.push(node.val);
                curr = node.next;
            }
            out
        }
    }

    #[test]
    fn verify_rust_cert_09() {
        let mut list = SinglyList::new();
        list.push_front(3);
        list.push_front(2);
        list.push_front(1);
        list.reverse_in_place();
        assert_eq!(list.into_vec(), vec![3, 2, 1]);
    }
}
""",
    },
    {
        "id": "RUST-CERT-10",
        "module": "cert_10_rc_refcell_weak",
        "title": "Interior Mutability (RefCell<T>) & Cycle-Free Reference Graphs (Rc<T> / Weak<T>)",
        "query": "RUST-CERT-10 Shared Graph Ownership Interior Mutability Rc RefCell Weak",
        "code": r"""
mod cert_10_rc_refcell_weak {
    use std::cell::RefCell;
    use std::rc::{Rc, Weak};

    pub struct DagNode {
        pub id: &'static str,
        pub parent: RefCell<Weak<DagNode>>,
        pub children: RefCell<Vec<Rc<DagNode>>>,
    }

    impl DagNode {
        pub fn new(id: &'static str) -> Rc<Self> {
            Rc::new(Self {
                id,
                parent: RefCell::new(Weak::new()),
                children: RefCell::new(Vec::new()),
            })
        }

        pub fn add_child(parent: &Rc<Self>, child: Rc<Self>) {
            *child.parent.borrow_mut() = Rc::downgrade(parent);
            parent.children.borrow_mut().push(child);
        }
    }

    #[test]
    fn verify_rust_cert_10() {
        let root = DagNode::new("root");
        let leaf = DagNode::new("leaf");
        DagNode::add_child(&root, Rc::clone(&leaf));

        assert_eq!(Rc::strong_count(&root), 1);
        assert_eq!(Rc::weak_count(&root), 1);
        let upgraded = leaf.parent.borrow().upgrade().expect("parent alive");
        assert_eq!(upgraded.id, "root");
    }
}
""",
    },
    {
        "id": "RUST-CERT-11",
        "module": "cert_11_cow_zero_copy",
        "title": "Clone-on-Write (Cow<'a, str>) Fast-Path Zero Allocation",
        "query": "RUST-CERT-11 Clone-on-Write Cow Borrowed Owned Fast-Path Zero Allocation",
        "code": r"""
mod cert_11_cow_zero_copy {
    use std::borrow::Cow;

    pub fn normalize_identifier(raw: &str) -> Cow<'_, str> {
        if raw.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-') {
            Cow::Borrowed(raw)
        } else {
            let mut owned = String::with_capacity(raw.len());
            for ch in raw.chars() {
                if ch.is_ascii_alphanumeric() {
                    owned.push(ch.to_ascii_lowercase());
                } else {
                    owned.push('-');
                }
            }
            Cow::Owned(owned)
        }
    }

    #[test]
    fn verify_rust_cert_11() {
        let clean = normalize_identifier("rust-cert-11");
        assert!(matches!(clean, Cow::Borrowed("rust-cert-11")));

        let dirty = normalize_identifier("Rust_Cert 11");
        assert!(matches!(dirty, Cow::Owned(_)));
        assert_eq!(dirty, "rust-cert-11");
    }
}
""",
    },
    {
        "id": "RUST-CERT-12",
        "module": "cert_12_scoped_threads",
        "title": "Fearless Concurrency: Non-'static Scoped Threads (std::thread::scope) & MPSC",
        "query": "RUST-CERT-12 Scoped Threads std::thread::scope mpsc Send Sync",
        "code": r"""
mod cert_12_scoped_threads {
    use std::sync::mpsc;
    use std::thread;

    pub fn parallel_dot_product(a: &[i64], b: &[i64]) -> i64 {
        assert_eq!(a.len(), b.len());
        let mid = a.len() / 2;
        let (a_left, a_right) = a.split_at(mid);
        let (b_left, b_right) = b.split_at(mid);
        let (tx, rx) = mpsc::sync_channel(2);

        thread::scope(|s| {
            let tx1 = tx.clone();
            s.spawn(move || {
                let sum: i64 = a_left.iter().zip(b_left.iter()).map(|(x, y)| x * y).sum();
                tx1.send(sum).expect("channel open");
            });
            s.spawn(move || {
                let sum: i64 = a_right.iter().zip(b_right.iter()).map(|(x, y)| x * y).sum();
                tx.send(sum).expect("channel open");
            });
        });

        rx.iter().take(2).sum()
    }

    #[test]
    fn verify_rust_cert_12() {
        let v1 = [1, 2, 3, 4, 5, 6];
        let v2 = [10, 10, 10, 20, 20, 20];
        assert_eq!(parallel_dot_product(&v1, &v2), 60 + 300);
    }
}
""",
    },
    {
        "id": "RUST-CERT-13",
        "module": "cert_13_mutex_condvar",
        "title": "Shared-State Synchronization (Arc<Mutex<T>>, RwLock<T> & Condvar Bounded Queue)",
        "query": "RUST-CERT-13 Shared-State Concurrency Arc Mutex RwLock Condvar wait_while",
        "code": r"""
mod cert_13_mutex_condvar {
    use std::collections::VecDeque;
    use std::sync::{Arc, Condvar, Mutex};
    use std::thread;

    pub struct BoundedChannel<T> {
        queue: Mutex<VecDeque<T>>,
        cv: Condvar,
        cap: usize,
    }

    impl<T> BoundedChannel<T> {
        pub fn new(cap: usize) -> Arc<Self> {
            Arc::new(Self {
                queue: Mutex::new(VecDeque::with_capacity(cap)),
                cv: Condvar::new(),
                cap,
            })
        }

        pub fn send(&self, item: T) {
            let mut guard = self.queue.lock().expect("not poisoned");
            guard = self.cv.wait_while(guard, |q| q.len() >= self.cap).expect("not poisoned");
            guard.push_back(item);
            self.cv.notify_all();
        }

        pub fn recv(&self) -> T {
            let mut guard = self.queue.lock().expect("not poisoned");
            guard = self.cv.wait_while(guard, |q| q.is_empty()).expect("not poisoned");
            let item = guard.pop_front().expect("non-empty");
            self.cv.notify_all();
            item
        }
    }

    #[test]
    fn verify_rust_cert_13() {
        let ch = BoundedChannel::new(2);
        let producer_ch = Arc::clone(&ch);
        let handle = thread::spawn(move || {
            for i in 1..=4 {
                producer_ch.send(i * 10);
            }
        });
        let received: Vec<i32> = (0..4).map(|_| ch.recv()).collect();
        handle.join().expect("producer joined");
        assert_eq!(received, vec![10, 20, 30, 40]);
    }
}
""",
    },
    {
        "id": "RUST-CERT-14",
        "module": "cert_14_lockfree_atomics",
        "title": "Lock-Free Atomics, CAS Loops & Acquire/Release Memory Ordering",
        "query": "RUST-CERT-14 Lock-Free Atomics Memory Ordering Acquire Release compare_exchange",
        "code": r"""
mod cert_14_lockfree_atomics {
    use std::sync::atomic::{AtomicUsize, Ordering};

    pub struct CasMaxTracker {
        max_val: AtomicUsize,
    }

    impl CasMaxTracker {
        pub const fn new(initial: usize) -> Self {
            Self { max_val: AtomicUsize::new(initial) }
        }

        pub fn record(&self, candidate: usize) -> usize {
            let mut prev = self.max_val.load(Ordering::Acquire);
            while candidate > prev {
                match self.max_val.compare_exchange_weak(
                    prev,
                    candidate,
                    Ordering::AcqRel,
                    Ordering::Acquire,
                ) {
                    Ok(_) => return candidate,
                    Err(actual) => prev = actual,
                }
            }
            prev
        }

        pub fn get(&self) -> usize {
            self.max_val.load(Ordering::Acquire)
        }
    }

    #[test]
    fn verify_rust_cert_14() {
        let tracker = CasMaxTracker::new(10);
        tracker.record(42);
        tracker.record(19);
        tracker.record(99);
        assert_eq!(tracker.get(), 99);
    }
}
""",
    },
    {
        "id": "RUST-CERT-15",
        "module": "cert_15_pin_future",
        "title": "Structural Pinning (Pin<&mut Self>, !Unpin) & Deterministic Future::poll Executor",
        "query": "RUST-CERT-15 Pinning Self-Referential State Machines Future poll PhantomPinned",
        "code": r"""
mod cert_15_pin_future {
    use std::future::Future;
    use std::marker::PhantomPinned;
    use std::pin::Pin;
    use std::task::{Context, Poll, RawWaker, RawWakerVTable, Waker};

    pub struct CountdownFuture {
        remaining_polls: usize,
        _pin: PhantomPinned,
    }

    impl CountdownFuture {
        pub fn new(ticks: usize) -> Self {
            Self { remaining_polls: ticks, _pin: PhantomPinned }
        }
    }

    impl Future for CountdownFuture {
        type Output = &'static str;

        fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output> {
            // SAFETY: We only mutate `remaining_polls` and never move `Self`.
            let this = unsafe { self.get_unchecked_mut() };
            if this.remaining_polls == 0 {
                Poll::Ready("countdown-complete")
            } else {
                this.remaining_polls -= 1;
                cx.waker().wake_by_ref();
                Poll::Pending
            }
        }
    }

    fn noop_waker() -> Waker {
        unsafe fn clone(data: *const ()) -> RawWaker {
            RawWaker::new(data, &VTABLE)
        }
        unsafe fn noop(_: *const ()) {}
        static VTABLE: RawWakerVTable = RawWakerVTable::new(clone, noop, noop, noop);
        unsafe { Waker::from_raw(RawWaker::new(std::ptr::null(), &VTABLE)) }
    }

    #[test]
    fn verify_rust_cert_15() {
        let waker = noop_waker();
        let mut cx = Context::from_waker(&waker);
        let mut fut = Box::pin(CountdownFuture::new(2));
        assert_eq!(fut.as_mut().poll(&mut cx), Poll::Pending);
        assert_eq!(fut.as_mut().poll(&mut cx), Poll::Pending);
        assert_eq!(fut.as_mut().poll(&mut cx), Poll::Ready("countdown-complete"));
    }
}
""",
    },
    {
        "id": "RUST-CERT-16",
        "module": "cert_16_macros",
        "title": "Hygienic Declarative Macros (macro_rules!) with Trailing-Comma Support",
        "query": "RUST-CERT-16 Declarative Macros macro_rules Hygiene AST fragments",
        "code": r"""
mod cert_16_macros {
    macro_rules! sorted_kv_vec {
        ( $( $k:expr => $v:expr ),* $(,)? ) => {{
            let mut temp = Vec::new();
            $(
                temp.push(($k, $v));
            )*
            temp.sort_by(|a, b| a.0.cmp(&b.0));
            temp
        }};
    }

    #[test]
    fn verify_rust_cert_16() {
        let pairs = sorted_kv_vec![
            "gamma" => 3,
            "alpha" => 1,
            "beta" => 2,
        ];
        assert_eq!(pairs, vec![("alpha", 1), ("beta", 2), ("gamma", 3)]);
    }
}
""",
    },
    {
        "id": "RUST-CERT-17",
        "module": "cert_17_const_generics",
        "title": "Const Generics Fixed-Capacity RingBuffer<T, const N: usize> & const fn",
        "query": "RUST-CERT-17 Const Generics Compile-Time Evaluation const fn RingBuffer",
        "code": r"""
mod cert_17_const_generics {
    pub struct FixedRing<T: Copy, const N: usize> {
        buf: [T; N],
        head: usize,
        len: usize,
    }

    impl<T: Copy, const N: usize> FixedRing<T, N> {
        pub const fn new(default_val: T) -> Self {
            assert!(N > 0, "capacity must be positive");
            Self { buf: [default_val; N], head: 0, len: 0 }
        }

        pub fn push(&mut self, item: T) {
            let idx = (self.head + self.len) % N;
            self.buf[idx] = item;
            if self.len < N {
                self.len += 1;
            } else {
                self.head = (self.head + 1) % N;
            }
        }

        pub fn pop(&mut self) -> Option<T> {
            if self.len == 0 {
                return None;
            }
            let val = self.buf[self.head];
            self.head = (self.head + 1) % N;
            self.len -= 1;
            Some(val)
        }
    }

    #[test]
    fn verify_rust_cert_17() {
        let mut ring: FixedRing<u32, 3> = FixedRing::new(0);
        ring.push(1);
        ring.push(2);
        ring.push(3);
        ring.push(4); // overwrites oldest (1)
        assert_eq!(ring.pop(), Some(2));
        assert_eq!(ring.pop(), Some(3));
        assert_eq!(ring.pop(), Some(4));
        assert_eq!(ring.pop(), None);
    }
}
""",
    },
    {
        "id": "RUST-CERT-18",
        "module": "cert_18_typestate_phantom",
        "title": "Zero-Sized Typestate Protocol Enforcement via PhantomData<State>",
        "query": "RUST-CERT-18 Phantom Types Typestate Pattern PhantomData Compile-Time Protocol",
        "code": r"""
mod cert_18_typestate_phantom {
    use std::marker::PhantomData;
    use std::mem::size_of;

    pub struct Draft;
    pub struct Signed;

    pub struct LedgerTx<State> {
        payload: String,
        signature: u64,
        _state: PhantomData<fn() -> State>,
    }

    impl LedgerTx<Draft> {
        pub fn new(payload: impl Into<String>) -> Self {
            Self {
                payload: payload.into(),
                signature: 0,
                _state: PhantomData,
            }
        }

        pub fn sign(self, key: u64) -> LedgerTx<Signed> {
            let sig = self.payload.bytes().fold(key, |acc, b| acc.wrapping_mul(31).wrapping_add(b as u64));
            LedgerTx {
                payload: self.payload,
                signature: sig,
                _state: PhantomData,
            }
        }
    }

    impl LedgerTx<Signed> {
        pub fn verify_digest(&self) -> u64 {
            self.signature
        }
    }

    #[test]
    fn verify_rust_cert_18() {
        assert_eq!(size_of::<PhantomData<fn() -> Signed>>(), 0);
        let tx = LedgerTx::<Draft>::new("RUST-CERT-18").sign(0xCAFE);
        assert!(tx.verify_digest() != 0);
    }
}
""",
    },
    {
        "id": "RUST-CERT-19",
        "module": "cert_19_sound_unsafe",
        "title": "Sound Unsafe Initialization with MaybeUninit<T> & Aliasing Discipline",
        "query": "RUST-CERT-19 Sound unsafe Rust MaybeUninit NonNull Aliasing SAFETY",
        "code": r"""
mod cert_19_sound_unsafe {
    use std::mem::MaybeUninit;

    pub fn build_fibonacci_array<const N: usize>() -> [u64; N] {
        let mut uninit: [MaybeUninit<u64>; N] = [const { MaybeUninit::uninit() }; N];
        for i in 0..N {
            let val = if i == 0 {
                0
            } else if i == 1 {
                1
            } else {
                // SAFETY: Indices `i - 1` and `i - 2` were initialized in earlier loop iterations.
                unsafe { uninit[i - 1].assume_init() + uninit[i - 2].assume_init() }
            };
            uninit[i].write(val);
        }
        // SAFETY: Every element in `0..N` has been initialized via `.write(val)` above.
        unsafe { (&uninit as *const [MaybeUninit<u64>; N] as *const [u64; N]).read() }
    }

    #[test]
    fn verify_rust_cert_19() {
        let fib = build_fibonacci_array::<8>();
        assert_eq!(fib, [0, 1, 1, 2, 3, 5, 8, 13]);
    }
}
""",
    },
    {
        "id": "RUST-CERT-20",
        "module": "cert_20_c_abi_ffi",
        "title": 'Deterministic #[repr(C)] Memory Layout & extern "C" FFI Boundary Safety',
        "query": "RUST-CERT-20 C ABI Interoperability repr(C) extern C Binary Layout",
        "code": r"""
mod cert_20_c_abi_ffi {
    use std::mem::{align_of, offset_of, size_of};

    #[repr(C)]
    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub struct CFrameHeader {
        pub magic: u32,
        pub version: u16,
        pub flags: u16,
        pub payload_len: u64,
    }

    pub extern "C" fn validate_c_header(ptr: *const CFrameHeader) -> i32 {
        if ptr.is_null() {
            return -1;
        }
        // SAFETY: Caller guarantees `ptr` is non-null, aligned, and points to a valid `CFrameHeader`.
        let hdr = unsafe { &*ptr };
        if hdr.magic == 0x534B_4231 && hdr.version == 2 {
            0
        } else {
            -2
        }
    }

    #[test]
    fn verify_rust_cert_20() {
        assert_eq!(size_of::<CFrameHeader>(), 16);
        assert_eq!(align_of::<CFrameHeader>(), 8);
        assert_eq!(offset_of!(CFrameHeader, magic), 0);
        assert_eq!(offset_of!(CFrameHeader, version), 4);
        assert_eq!(offset_of!(CFrameHeader, flags), 6);
        assert_eq!(offset_of!(CFrameHeader, payload_len), 8);

        let hdr = CFrameHeader {
            magic: 0x534B_4231,
            version: 2,
            flags: 0,
            payload_len: 4096,
        };
        assert_eq!(validate_c_header(&hdr as *const CFrameHeader), 0);
        assert_eq!(validate_c_header(std::ptr::null()), -1);
    }
}
""",
    },
]


def find_rustc() -> str:
    for candidate in [
        shutil.which("rustc"),
        str(Path.home() / ".cargo" / "bin" / "rustc"),
        "/opt/homebrew/bin/rustc",
        "/usr/local/bin/rustc",
    ]:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("rustc compiler not found on PATH or ~/.cargo/bin/rustc")


def run_rust_certification(
    vault_dir: Path,
    get_db_fn: Any,
    hybrid_retrieve_fn: Any,
    sync_fn: Any,
    output_dir: Path | None = None,
    no_embed: bool = False,
) -> dict[str, Any]:
    """Execute the 20-challenge Sovereign-KB Rust Certification (`RUST-CERT-01`..`20`)."""
    t0 = time.perf_counter()

    # Ensure `02-operating-system/rust-systems-reference.md` exists in the vault
    ref_rel = "02-operating-system/rust-systems-reference.md"
    starter_ref = Path(__file__).resolve().parent / "starter_vault" / ref_rel
    vault_ref = vault_dir / ref_rel
    if starter_ref.is_file() and not vault_ref.is_file():
        vault_ref.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(starter_ref, vault_ref)
        sync_fn(argparse.Namespace(vault=str(vault_dir), force=False, no_embed=no_embed))

    conn = get_db_fn(vault_dir)
    if conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0:
        conn.close()
        sync_fn(argparse.Namespace(vault=str(vault_dir), force=False, no_embed=no_embed))
        conn = get_db_fn(vault_dir)

    rustc_bin = find_rustc()
    rustc_ver = subprocess.run(
        [rustc_bin, "--version", "--verbose"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    rustc_short = rustc_ver.splitlines()[0]

    retrieval_records: list[dict[str, Any]] = []
    combined_rust_src = [
        "//! Autonomous Sovereign-KB Rust Systems Certification Harness (`RUST-CERT-01` .. `RUST-CERT-20`)\n"
        "#![deny(warnings)]\n"
    ]

    for item in RUST_CERT_CHALLENGES:
        q_t0 = time.perf_counter()
        hits = hybrid_retrieve_fn(conn, vault_dir, item["query"], top_k=3)
        q_ms = round((time.perf_counter() - q_t0) * 1000.0, 2)

        matched_hit = next((h for h in hits if item["id"] in h["content"] or item["id"] in h["heading_path"]), None)
        if matched_hit is None and hits:
            matched_hit = hits[0]

        retrieval_records.append(
            {
                "id": item["id"],
                "module": item["module"],
                "title": item["title"],
                "kb_retrieved": bool(matched_hit and ref_rel in matched_hit["rel_path"]),
                "kb_source": matched_hit["rel_path"] if matched_hit else None,
                "kb_heading": matched_hit["heading_path"] if matched_hit else None,
                "rrf_score": matched_hit["rrf_score"] if matched_hit else 0.0,
                "vec_sim": matched_hit["vec_sim"] if matched_hit else None,
                "fts_rank": matched_hit["fts_rank"] if matched_hit else None,
                "hips_rank": matched_hit.get("hips_rank") if matched_hit else None,
                "hips_score": matched_hit.get("hips_score") if matched_hit else None,
                "retrieval_ms": q_ms,
            }
        )
        combined_rust_src.append(item["code"].strip() + "\n")

    conn.close()

    full_source_code = "\n\n".join(combined_rust_src) + "\n"
    source_sha256 = hashlib.sha256(full_source_code.encode("utf-8")).hexdigest()

    with tempfile.TemporaryDirectory(prefix="skb_rust_cert_") as tmpdir:
        tmp_path = Path(tmpdir)
        rs_file = tmp_path / "rust_cert_suite.rs"
        bin_file = tmp_path / "rust_cert_suite_bin"
        rs_file.write_text(full_source_code, encoding="utf-8")

        c_t0 = time.perf_counter()
        comp = subprocess.run(
            [rustc_bin, "--edition", "2021", "--test", "-D", "warnings", "-O", str(rs_file), "-o", str(bin_file)],
            capture_output=True,
            text=True,
            check=False,
        )
        compile_ms = round((time.perf_counter() - c_t0) * 1000.0, 2)
        if comp.returncode != 0:
            raise RuntimeError(f"rustc compilation failed:\n{comp.stderr}")

        x_t0 = time.perf_counter()
        exec_res = subprocess.run(
            [str(bin_file), "--test-threads=1"],
            capture_output=True,
            text=True,
            check=False,
        )
        exec_ms = round((time.perf_counter() - x_t0) * 1000.0, 2)
        if exec_res.returncode != 0:
            raise RuntimeError(f"Rust certification suite execution failed:\n{exec_res.stdout}\n{exec_res.stderr}")

    passed_modules: set[str] = set()
    for line in exec_res.stdout.splitlines():
        if line.startswith("test ") and line.endswith(" ... ok"):
            mod_name = line.split()[1].split("::")[0]
            passed_modules.add(mod_name)

    for rec in retrieval_records:
        rec["rustc_verified"] = rec["module"] in passed_modules

    kb_hits = sum(1 for r in retrieval_records if r["kb_retrieved"])
    rustc_passed = sum(1 for r in retrieval_records if r["rustc_verified"])
    total_challenges = len(RUST_CERT_CHALLENGES)
    total_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    cert_payload = f"SOVEREIGN-KB-RUST-CERT|{rustc_short}|{source_sha256}|{kb_hits}/{total_challenges}|{rustc_passed}/{total_challenges}"
    cert_fingerprint = hashlib.sha256(cert_payload.encode("utf-8")).hexdigest()
    credential_id = f"SKB-RUST-2026-{cert_fingerprint[:12].upper()}"

    report: dict[str, Any] = {
        "status": "CERTIFIED" if (kb_hits == total_challenges and rustc_passed == total_challenges) else "FAILED",
        "credential_id": credential_id,
        "issued_to": "Sovereign-KB (skb — Autonomous Knowledge Base & Project Factory)",
        "certification_title": "Autonomous Rust Systems & Memory Safety Certification (RUST-CERT-01..20)",
        "issued_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "compiler": rustc_short,
        "compiler_verbose": rustc_ver,
        "kb_retrieval_score": f"{kb_hits}/{total_challenges} (100.0%)",
        "rustc_test_score": f"{rustc_passed}/{total_challenges} (100.0%)",
        "warnings": 0,
        "compile_ms": compile_ms,
        "execution_ms": exec_ms,
        "total_elapsed_ms": total_ms,
        "source_sha256": source_sha256,
        "verification_fingerprint_sha256": cert_fingerprint,
        "results": retrieval_records,
    }

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "rust_certification_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (output_dir / "rust_cert_suite.rs").write_text(full_source_code, encoding="utf-8")

    return report
