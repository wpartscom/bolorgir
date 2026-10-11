//! Process-wide `std.Io` instance for the core.
//!
//! The public C ABI has no parameter through which a caller could supply an
//! I/O implementation, so the core owns one instance with static lifetime.
//! The single-threaded variant is used: it needs no allocator, spawns no
//! worker threads, and disables cancelation, so blocking file and futex
//! operations run synchronously. This matches the blocking semantics of
//! the plain thread primitives the core used previously.
//!
//! The instance may be shared by every thread in the process (C callers,
//! Python, the core's own workers): `io()` only returns a pointer plus a
//! vtable, and the underlying operations are futex/syscall based.
//!
//! `Mutex` binds that instance into the classic lock/unlock pair. Locking
//! is uncancelable and charges no work budget: the internal mutexes protect
//! short critical sections and must never observe engine cancelation.

const std = @import("std");

var threaded_instance: std.Io.Threaded = .init_single_threaded;

/// Returns the shared instance. Cheap; safe from any thread.
pub fn io() std.Io {
    return threaded_instance.io();
}

/// Blocking mutex over the shared instance. `lock` maps to
/// `Io.Mutex.lockUncancelable` and `unlock` to `Io.Mutex.unlock`.
pub const Mutex = struct {
    inner: std.Io.Mutex = .init,

    pub fn lock(self: *Mutex) void {
        self.inner.lockUncancelable(io());
    }

    pub fn unlock(self: *Mutex) void {
        self.inner.unlock(io());
    }

    pub fn tryLock(self: *Mutex) bool {
        return self.inner.tryLock();
    }
};
