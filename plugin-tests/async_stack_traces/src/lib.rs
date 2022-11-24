use std::{sync::Arc, task::Waker};

use futures::task::{waker, ArcWake};

pub fn dummy_waker() -> Waker {
    struct DummyWaker;
    impl ArcWake for DummyWaker {
        fn wake_by_ref(_: &Arc<Self>) {
            // noop
        }
    }

    waker(Arc::new(DummyWaker))
}

#[inline(never)]
pub fn zzz<T>(v: *const T) {
    unsafe {
        let _ = std::ptr::read_volatile(v);
    }
}
