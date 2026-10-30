---
title: "Rust Systems, Ownership & Memory Safety Reference (RUST-CERT-01..20)"
type: operating-system
updated: 2026-10-26
tags: [rust, systems, ownership, lifetimes, borrow-checker, concurrency, atomics, ffi, rust-cert]
related:
  - "[[02-operating-system/archetypes/README-archetypes]]"
  - "[[02-operating-system/autonomous-engineering-framework]]"
  - "[[02-operating-system/codebase-governance-and-sizing]]"
  - "[[03-life-domains/ventures-and-engineering]]"
  - "[[INDEX]]"
---

# Rust Systems, Ownership & Memory Safety Reference (`RUST-CERT-01` .. `RUST-CERT-20`)

This operating manual encodes the canonical memory-safety invariants, type-system guarantees, concurrency primitives, and systems-programming idioms required by the [[02-operating-system/archetypes/README-archetypes|rust-systems Project Archetype]] and the deterministic **`sovereign-kb certify rust`** compiler verification suite (`RUST-CERT-01` through `RUST-CERT-20`).

---

## 1. Ownership, Borrowing & Lifetimes (`RUST-CERT-01` .. `RUST-CERT-02`)

### RUST-CERT-01: Ownership, Move Semantics & Borrowing Invariants (`E0382`, `E0502`)
- **Affine Ownership Invariant**: Every value in Rust has a single owner. When the owner goes out of scope, `Drop::drop` runs deterministically (RAII). Assignment or passing by value transfers ownership (**move semantics**) unless the type implements `Copy` (stack-only bitwise copy, e.g., primitives, shared references `&T`, tuples of `Copy` types).
- **The Law of Exclusivity (Aliasing XOR Mutability)**: At any program point under Non-Lexical Lifetimes (NLL), a resource may have either:
  1. Any number of shared immutable references (`&T`), **or**
  2. Exactly one exclusive mutable reference (`&mut T`).
- **Slice Reborrowing (`split_at_mut`)**: To mutate disjoint segments of a slice simultaneously without triggering `E0499` (multiple mutable borrows), partition via `slice.split_at_mut(mid)`.

### RUST-CERT-02: Explicit Lifetimes, Variance & Higher-Ranked Trait Bounds (`E0106`, `E0597`)
- **Lifetime Elision Rules**:
  1. Each elided input reference gets a distinct lifetime parameter (`fn f<'a, 'b>(x: &'a str, y: &'b str)`).
  2. If there is exactly one input lifetime parameter, it is assigned to all elided output lifetimes.
  3. If `&self` or `&mut self` is present, the lifetime of `self` is assigned to all elided output lifetimes.
- **Zero-Copy View Structs**: A struct holding a borrow `struct ZeroCopyToken<'a> { raw: &'a str }` cannot outlive `'a` (`'a: 'struct`). When parsing a mutable buffer or advancing a cursor `'a`, decouple the immutable source slice lifetime `'src` from the mutable parser borrow `'p` (`fn next_token<'p>(&'p mut self) -> Option<&'src str>`).
- **Higher-Ranked Trait Bounds (HRTB)**: Use `for<'a> F: Fn(&'a str) -> &'a str` when a closure must work for *any* lifetime `'a` chosen inside the callee rather than a single fixed caller lifetime.

---

## 2. Type System, Algebraic Data Types & Error Handling (`RUST-CERT-03` .. `RUST-CERT-06`)

### RUST-CERT-03: Algebraic Data Types, Pattern Matching & Exhaustiveness (`E0004`)
- **Tagged Unions & Niche Optimization**: `enum` types store a discriminant plus the largest variant payload. `Option<&T>`, `Option<Box<T>>`, and `Option<NonZeroU64>` occupy the exact same size as the inner pointer/integer (`size_of::<Option<&T>>() == size_of::<&T>()`) via the null-pointer niche optimization.
- **Exhaustive Destructuring**: Use slice patterns (`[head, middle @ .., tail]`), `@` subpattern bindings (`val @ 1..=99`), and guard clauses to enforce compile-time totality without panicking indexing.

### RUST-CERT-04: Composable Error Handling, `?` Propagation & `From` Conversion
- **Zero-Panic Error Discipline**: Production library and kernel paths forbid `.unwrap()` / `.expect()`. Propagate errors via `Result<T, E>` and the `?` operator, which desugars to `From::from(err)`.
- **Domain Error Hierarchy**: Implement `std::fmt::Display`, `std::error::Error` (with `source()`), and `From<LowerError>` so heterogeneous failure modes compose cleanly across module boundaries.

### RUST-CERT-05: Generics, Trait Bounds, Associated Types & `dyn` Object Safety (`E0277`)
- **Monomorphization vs. Dynamic Dispatch**: Generic functions (`fn process<T: Codec>(x: &T)`) generate specialized machine code per concrete type with zero vtable overhead. Trait objects (`&dyn Codec`, `Box<dyn Codec>`) use a fat pointer `(data_ptr, vtable_ptr)` for heterogeneous runtime collections.
- **Object Safety (`dyn` Compatibility)**: A trait is `dyn`-compatible only if its methods do not return `Self`, do not carry generic type parameters on methods, and do not require `Self: Sized` (unless opted out per method via `where Self: Sized`).
- **Associated Types vs. Generic Parameters**: Use associated types (`trait Graph { type Node; }`) when there is a single canonical output type per implementation; use generic parameters (`trait From<T>`) when a type implements the trait for multiple input types.

### RUST-CERT-06: Standard Conversion Traits, Operator Overloading & Newtype Idiom
- **Canonical Conversions**: Implement `From<A> for B` (which automatically provides `Into<B> for A` via blanket implementation) and `TryFrom<A> for B` for fallible domain validation.
- **Ergonomic API Borrowing**: Accept `impl AsRef<str>` or `impl AsRef<Path>` for read-only inspection, and `Borrow<Q>` for hash/tree lookup keys (`HashMap<String, V>::get(&str)`).
- **Zero-Cost Newtypes (`#[repr(transparent)]`)**: Wrap primitive units (`struct Meters(f64)`, `struct Seconds(f64)`) with `std::ops::Add`, `Sub`, and `Div` to prevent dimensional mismatch at compile time.

---

## 3. Iterators, Closures & Smart Pointers (`RUST-CERT-07` .. `RUST-CERT-11`)

### RUST-CERT-07: Zero-Cost Iterators, Adapters & Custom `Iterator` State Machines
- **Lazy Pipeline Compilation**: Chains of `.iter().filter(...).map(...).fold(...)` fuse into a single tight loop with zero intermediate heap allocations.
- **Short-Circuiting Fallible Collection**: `Iterator::collect::<Result<Vec<T>, E>>()` halts on the first `Err(e)` without evaluating remaining elements.
- **Custom State Machine Iterators**: Implementing `Iterator` (`type Item`, `fn next(&mut self) -> Option<Self::Item>`) and `DoubleEndedIterator` (`fn next_back(&mut self)`) enables bidirectional zero-allocation traversal.

### RUST-CERT-08: Closures, Capture Modes & `Fn` / `FnMut` / `FnOnce` Hierarchy
- **Trait Subtyping**: Every `Fn` implements `FnMut`, and every `FnMut` implements `FnOnce` (`Fn: FnMut: FnOnce`).
  - `Fn(&self)`: Captures environment by shared reference (`&T`); callable concurrently and repeatedly.
  - `FnMut(&mut self)`: Captures environment by exclusive mutable reference (`&mut T`); stateful accumulators and middlewares.
  - `FnOnce(self)`: Consumes captured values by move; callable at most once.

### RUST-CERT-09: Heap Indirection, Recursive Data Structures & In-Place Mutation (`Box<T>`)
- **Recursive Type Sizing (`E0072`)**: Recursive types (`enum Expr`, `struct Node<T>`) require pointer indirection (`Box<T>`) so `rustc` can compute a finite stack size.
- **Safe In-Place Ownership Transfer (`std::mem::take` / `replace`)**: When mutating an owned field behind `&mut self` (such as reversing a linked list or rotating a binary search tree), use `std::mem::take(&mut node.next)` or `std::mem::replace` to extract ownership temporarily without `Clone` or `unsafe`.

### RUST-CERT-10: Shared Graph Ownership & Interior Mutability (`Rc<T>`, `RefCell<T>`, `Weak<T>`)
- **Interior Mutability (`RefCell<T>` & `Cell<T>`)**: Moves borrow-checking from compile time to runtime for single-threaded graphs/DAGs. `borrow()` increments an immutable reader count; `borrow_mut()` asserts zero active readers/writers and returns `RefMut<T>`.
- **Acyclic Reference Graphs (`Weak<T>`)**: Parent-to-child edges hold strong `Rc<Node>`, while child-to-parent or observer back-edges hold `Weak<Node>` (accessed via `.upgrade() -> Option<Rc<Node>>`) to guarantee deterministic deallocation with zero memory leaks.

### RUST-CERT-11: Clone-on-Write (`Cow<'a, B>`) & Zero-Copy Slice Windowing
- **Fast-Path Zero Allocation (`Cow<'a, str>`)**: Return `Cow::Borrowed(input)` when an input string requires no escaping or normalization, and allocate `Cow::Owned(String)` only when a mutation is actually triggered.

---

## 4. Fearless Concurrency, Atomics & Async Pinning (`RUST-CERT-12` .. `RUST-CERT-15`)

### RUST-CERT-12: Scoped Threads & Bounded Message Passing (`std::thread::scope`, `mpsc`)
- **Non-`'static` Parallelism (`std::thread::scope`)**: `thread::scope(|s| { ... })` guarantees all spawned worker threads join before the scope exits, allowing workers to borrow local stack slices (`&[T]`, `&mut [T]`) directly without `Arc` or heap cloning.
- **`Send` and `Sync` Auto-Traits**:
  - `T: Send` iff transferring ownership of `T` to another thread is data-race free.
  - `T: Sync` iff `&T: Send` (sharing a reference across threads is data-race free). `Rc<T>` and `RefCell<T>` are `!Send` and `!Sync`.

### RUST-CERT-13: Shared-State Concurrency & Synchronization (`Arc<Mutex<T>>`, `RwLock<T>`, `Condvar`)
- **Data-Carrying Locks**: In Rust, `Mutex<T>` and `RwLock<T>` wrap the protected data `T` directly—the lock cannot be forgotten before accessing `T`.
- **Minimize Lock Contention**: Drop `MutexGuard` before performing expensive I/O or compute, and pair `Mutex<State>` with `Condvar::wait_while(guard, |state| ...)` for zero-busy-wait producer/consumer queues.

### RUST-CERT-14: Lock-Free Atomics & Memory Ordering (`AtomicUsize`, `Acquire` / `Release` / `SeqCst`)
- **Happens-Before Synchronization**:
  - `Ordering::Relaxed`: Atomic modification order on a single variable only; suitable for monotonic telemetry counters.
  - `Ordering::Release` (store) paired with `Ordering::Acquire` (load): Establishes a happens-before edge; all memory writes prior to the `Release` store in Thread A become visible after the `Acquire` load in Thread B.
  - `compare_exchange(current, new, success_order, failure_order)`: Foundation for CAS loops, lock-free stacks, and ticket locks.

### RUST-CERT-15: Pinning, Self-Referential State Machines & `Future` Polling (`Pin<&mut Self>`)
- **Structural Pinning (`Pin<P>`)**: Async blocks compile into state-machine structs that may hold references across `.await` points into their own fields. `Pin<&mut T>` forbids obtaining `&mut T` (preventing `std::mem::swap`) whenever `T: !Unpin` (marked via `PhantomPinned`).
- **Custom `Future` Implementation**: Implement `fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output>` to drive deterministic non-blocking state transitions without heap allocation.

---

## 5. Metaprogramming, Const Generics, Typestate & Safe FFI (`RUST-CERT-16` .. `RUST-CERT-20`)

### RUST-CERT-16: Declarative Macros (`macro_rules!`) & Hygiene
- **Hygienic Syntax Extension**: `macro_rules!` matches AST fragments (`$e:expr`, `$i:ident`, `$t:ty`, `$($k:expr => $v:expr),* $(,)?`) and expands them prior to type checking while keeping local macro identifiers hygienic.

### RUST-CERT-17: Const Generics & Compile-Time Evaluation (`const fn`, `[T; N]`)
- **Zero-Heap Fixed-Capacity Containers**: Parameterize structs over values (`struct RingBuffer<T, const N: usize>`) and compute lookup tables at compile time via `const fn`.

### RUST-CERT-18: Phantom Types & The Typestate Pattern (`PhantomData<State>`)
- **Compile-Time State Machine Enforcement**: Use zero-sized marker structs (`struct Unsealed; struct Sealed;`) and `PhantomData<fn() -> State>` on generic structs (`struct Packet<State>`) so illegal state transitions (e.g., transmitting an unsealed frame) fail to compile with zero runtime overhead (`size_of::<PhantomData<State>>() == 0`).

### RUST-CERT-19: Sound `unsafe` Rust, `MaybeUninit<T>`, `NonNull<T>` & Aliasing Discipline
- **The 5 Unsafe Operations**: Dereferencing raw pointers (`*const T`, `*mut T`), calling `unsafe fn`, accessing/modifying mutable statics, implementing `unsafe trait`, and accessing `union` fields.
- **Uninitialized Memory Safety (`MaybeUninit<T>`)**: Never create a reference to uninitialized memory (instant Undefined Behavior). Use `[MaybeUninit<T>; N]`, initialize elements via `.write(val)`, and document the exact invariant in a `// SAFETY:` block before calling `assume_init()`.

### RUST-CERT-20: C ABI Interoperability (`#[repr(C)]`, `extern "C"`) & Binary Layout
- **Deterministic Memory Layout (`#[repr(C)]`)**: Rust's default `#[repr(Rust)]` reorders struct fields to minimize padding. Use `#[repr(C)]` for FFI structs and hardware descriptors to guarantee C-ABI field order, predictable `size_of` / `align_of`, and safe pointer interop across `extern "C"` boundaries.
