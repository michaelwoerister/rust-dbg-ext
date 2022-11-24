

import gdb
import re

gdb.rs_async_stack_trace_node_factory = []
gdb.sub_future_resolvers = []


def mk_stack_trace_node(value: gdb.Value):
    """
    Given the gdb.Value of something implementing Future,
    create an AsyncStackTraceNode for it.

    This function is extensible via register_stack_trace_provider.

    There is a provider already that handles async fns and blocks.
    Further providers can be registered for custom futures.
    """
    for matcher, factory_fn in gdb.rs_async_stack_trace_node_factory:
        if matcher(value):
            return factory_fn(value)

    return AsyncStackTraceNode(value)


def register_stack_trace_provider(matcher, factory_fn):
    gdb.rs_async_stack_trace_node_factory.append((matcher, factory_fn))


def register_sub_future_resolver(resolver_fn):
    gdb.sub_future_resolvers.append(resolver_fn)


def resolve_sub_futures(awaitee: gdb.Value):
    """
    The function resolves an awaitee to the set of sub-futures that get
    their own frame in the stack trace. In the trivial case, the awaitee
    is already the sub-future (e.g. when .awaiting an async fn in another
    async fn). But there are a few cases, we further resolution needs to happen:

     - When the future is behind a pointer or some other wrapper like `Pin`.
       In that case we need to dereference the pointer.
     - When the future is behind a trait pointer. Then we need to downcast
       the pointer to the concrete type by looking at the vtable. This is
       not yet implemented.
     - When the awaitee is a combinator (e.g. a select or a join), then we
       need to decode the combinator object and extract the actual futures.
       There is only one sample implementation for
       `futures_concurrency::future::race::array::Race`.

    This function is set up to be extensible via `register_sub_future_resolver`.
    That way, new and custom data types can be supported.
    """
    for resolver_fn in gdb.sub_future_resolvers:
        resolved = None
        try:
            resolved = resolver_fn(awaitee)
        except:
            resolved = None

        if resolved:
            return resolved

    return SubFutures(SUB_FUTURES_SIMPLE, [awaitee])


SUB_FUTURES_SIMPLE = 0
SUB_FUTURES_JOIN = 1
SUB_FUTURES_SELECT = 2


class SubFutures:
    def __init__(self, kind, sub_futures):
        self.kind = kind
        self.items = sub_futures


class AsyncStackTraceNode:
    """
    This is the base class for nodes in an async stack trace tree.
    Each node needs to provide a label to be printed and its awaitee.
    Override the methods below as needed.
    """

    def __init__(self, root_value: gdb.Value):
        self.root_value = root_value

    def label(self) -> str:
        try:
            return self.root_value.type.name
        except:
            return "<unknown>"

    def awaitee(self):
        return None


RE_ASYNC_FN_ENV = re.compile("::\{async_fn_env#\d+\}")


class GenFutureNode(AsyncStackTraceNode):
    """
    The AsyncStackTraceNode implementing for all async fns and blocks.
    """

    @staticmethod
    def matches(value: gdb.Value) -> bool:
        return value.type.name.startswith('core::future::from_generator::GenFuture<')

    def __init__(self, gen_future):
        super().__init__(gen_future)
        inner = gen_future["__0"]
        for field in inner.type.fields():
            if field.name.isnumeric():
                self.state = inner[field]
                return

    def label(self) -> str:
        name = self.root_value["__0"].type.name

        fields = {}
        for field in self.state.type.fields():
            if field.name != "__awaitee":
                fields[field.name] = field

        if len(fields) > 0:
            name += " ["
            for index, field in enumerate(fields.values()):
                name += field.name
                name += "="
                name += str(self.state[field])
                if index != len(fields) - 1:
                    name += ", "
            name += "]"

        return RE_ASYNC_FN_ENV.sub("()", name)

    def awaitee(self):
        for field in self.state.type.fields():
            if field.name == "__awaitee":
                return self.state[field]


register_stack_trace_provider(GenFutureNode.matches, GenFutureNode)


def pin_resolver(value: gdb.Value):
    if value.type.name.startswith("core::pin::Pin<"):
        return resolve_sub_futures(value["pointer"].dereference())


register_sub_future_resolver(pin_resolver)


# NOTE: This does not handle fat pointers, like `&dyn Future<...>`.
def ptr_resolver(value: gdb.Value):
    if value.type.code == gdb.TYPE_CODE_PTR or value.type.code == gdb.TYPE_CODE_REF:
        return resolve_sub_futures(value.dereference())


register_sub_future_resolver(ptr_resolver)


def array_race_resolver(value: gdb.Value):
    """
    This function extracts the sub-futures out of a futures-concurrency select.
    """

    if value.type.code == gdb.TYPE_CODE_STRUCT and value.type.name.startswith("futures_concurrency::future::race::array::Race<"):
        futures = value["futures"]
        start, end = futures.type.range()
        sub_futures = []
        for index in range(start, end + 1):
            child = resolve_sub_futures(futures[index])
            if child.kind != SUB_FUTURES_SIMPLE:
                raise "unsupported"

            sub_futures.append(child.items[0])

        return SubFutures(SUB_FUTURES_SELECT, sub_futures)
    else:
        return None


register_sub_future_resolver(array_race_resolver)


def print_stack_trace(pp, indent=0, prefix=""):
    pp = mk_stack_trace_node(pp)

    if not pp:
        return

    for _ in range(0, indent):
        gdb.write(" ")

    gdb.write(prefix)

    # Print the name of the async fn / Future
    gdb.write(pp.label())
    gdb.write("\n")

    indent = indent + len(prefix)

    awaitee = pp.awaitee()
    if awaitee:
        sub_futures = resolve_sub_futures(awaitee)
        if sub_futures.kind == SUB_FUTURES_SIMPLE:
            assert (len(sub_futures.items) == 1)
            print_stack_trace(sub_futures.items[0], indent + 2)
        elif sub_futures.kind == SUB_FUTURES_SELECT:
            for _ in range(0, indent):
                gdb.write(" ")
            gdb.write("(SELECT)\n")
            for sub_future in sub_futures.items:
                print_stack_trace(sub_future, indent, "=> ")
        elif sub_futures.kind == SUB_FUTURES_JOIN:
            raise "Joins not yet implemented"
        else:
            raise "Unknown SubFutures kind"

    if indent == 0:
        gdb.flush()


class PrintAsyncStackTraceCli(gdb.Command):
    def __init__(self):
        super(PrintAsyncStackTraceCli, self).__init__(
            "print-stack-trace", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        root_future = resolve_sub_futures(gdb.parse_and_eval(arg))
        print_stack_trace(root_future.items[0], 0)


# Register the CLI command with GDB
PrintAsyncStackTraceCli()
