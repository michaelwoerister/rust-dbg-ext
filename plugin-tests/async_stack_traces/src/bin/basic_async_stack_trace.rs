/***

#if @gdb
  run
  #check Breakpoint @{ .* }@ main @{ .* }@ at @{ .* }@ basic_async_stack_trace.rs:

  print-stack-trace future
  #check basic_async_stack_trace::foo() [x=42]
  #check   basic_async_stack_trace::bar() [a=86, x=43]
  #check   (SELECT)
  #check   => basic_async_stack_trace::baz() [x=44, y=88]
  #check        futures_util::future::pending::Pending<u32>
  #check   => basic_async_stack_trace::baz() [x=88, y=176]
  #check        futures_util::future::pending::Pending<u32>
  #check   => basic_async_stack_trace::baz() [x=46, y=92]
  #check        futures_util::future::pending::Pending<u32>

***/

use std::task::{Context, Poll};

use async_stack_traces::{dummy_waker, zzz};
use futures::FutureExt;
use futures_concurrency::future::Race;

async fn foo(x: u32) -> u32 {
    bar(x + 1).await + 7
}

async fn bar(x: u32) -> u32 {
    let a = x * 2;
    [baz(x + 1), baz(a + 2), baz(x + 3)].race().await * 7 + a
}

async fn baz(x: u32) -> u32 {
    let y = x << 1;
    futures::future::pending::<u32>().await + x * y
}

fn main() {
    let mut future = Box::pin(foo(42));
    let waker = dummy_waker();
    let cx = &mut Context::from_waker(&waker);

    assert_eq!(future.poll_unpin(cx), Poll::Pending);

    zzz(&future); // #break
}
