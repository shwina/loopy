from __future__ import division

__copyright__ = "Copyright (C) 2012 Andreas Kloeckner"

__license__ = """
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""




import numpy as np
import loopy as lp
import pyopencl as cl

from pyopencl.tools import pytest_generate_tests_for_pyopencl \
        as pytest_generate_tests

__all__ = ["pytest_generate_tests",
    "cl" # 'cl.create_some_context'
    ]




def test_type_inference_no_artificial_doubles(ctx_factory):
    ctx = ctx_factory()

    knl = lp.make_kernel(ctx.devices[0],
            "{[i]: 0<=i<n}",
            """
                <> bb = a[i] - b[i]
                c[i] = bb
                """,
            [
                lp.GlobalArg("a", np.float32, shape=("n",)),
                lp.GlobalArg("b", np.float32, shape=("n",)),
                lp.GlobalArg("c", np.float32, shape=("n",)),
                lp.ValueArg("n", np.int32),
                ],
            assumptions="n>=1")

    for k in lp.generate_loop_schedules(knl):
        code = lp.generate_code(k)
        assert "double" not in code




def test_sized_and_complex_literals(ctx_factory):
    ctx = ctx_factory()

    knl = lp.make_kernel(ctx.devices[0],
            "{[i]: 0<=i<n}",
            """
                <> aa = 5jf
                <> bb = 5j
                a[i] = imag(aa)
                b[i] = imag(bb)
                c[i] = 5f
                """,
            [
                lp.GlobalArg("a", np.float32, shape=("n",)),
                lp.GlobalArg("b", np.float32, shape=("n",)),
                lp.GlobalArg("c", np.float32, shape=("n",)),
                lp.ValueArg("n", np.int32),
                ],
            assumptions="n>=1")

    lp.auto_test_vs_ref(knl, ctx, lp.generate_loop_schedules(knl),
            parameters=dict(n=5))




def test_simple_side_effect(ctx_factory):
    ctx = ctx_factory()

    knl = lp.make_kernel(ctx.devices[0],
            "{[i,j]: 0<=i,j<100}",
            """
                a[i] = a[i] + 1
                """,
            [lp.GlobalArg("a", np.float32, shape=(100,))]
            )

    kernel_gen = lp.generate_loop_schedules(knl)
    kernel_gen = lp.check_kernels(kernel_gen)

    for gen_knl in kernel_gen:
        print gen_knl
        compiled = lp.CompiledKernel(ctx, gen_knl)
        print compiled.code




def test_nonsense_reduction(ctx_factory):
    ctx = ctx_factory()

    knl = lp.make_kernel(ctx.devices[0],
            "{[i]: 0<=i<100}",
            """
                a[i] = sum(i, 2)
                """,
            [lp.GlobalArg("a", np.float32, shape=(100,))]
            )

    import pytest
    with pytest.raises(RuntimeError):
        list(lp.generate_loop_schedules(knl))




def test_owed_barriers(ctx_factory):
    ctx = ctx_factory()

    knl = lp.make_kernel(ctx.devices[0],
            "{[i]: 0<=i<100}",
            [
                "[i:l.0] <float32> z[i] = a[i]"
                ],
            [lp.GlobalArg("a", np.float32, shape=(100,))]
            )

    kernel_gen = lp.generate_loop_schedules(knl)
    kernel_gen = lp.check_kernels(kernel_gen)

    for gen_knl in kernel_gen:
        compiled = lp.CompiledKernel(ctx, gen_knl)
        print compiled.code




def test_wg_too_small(ctx_factory):
    ctx = ctx_factory()

    knl = lp.make_kernel(ctx.devices[0],
            "{[i]: 0<=i<100}",
            [
                "[i:l.0] <float32> z[i] = a[i] {id=copy}"
                ],
            [lp.GlobalArg("a", np.float32, shape=(100,))],
            local_sizes={0: 16})

    kernel_gen = lp.generate_loop_schedules(knl)
    kernel_gen = lp.check_kernels(kernel_gen)

    for gen_knl in kernel_gen:
        try:
            lp.CompiledKernel(ctx, gen_knl)
        except RuntimeError, e:
            assert "implemented and desired" in str(e)
            pass # expected!
        else:
            assert False # expecting an error




def test_join_inames(ctx_factory):
    ctx = ctx_factory()

    knl = lp.make_kernel(ctx.devices[0],
            "{[i,j]: 0<=i,j<16}",
            [
                "b[i,j] = 2*a[i,j]"
                ],
            [
                lp.GlobalArg("a", np.float32, shape=(16, 16,)),
                lp.GlobalArg("b", np.float32, shape=(16, 16,))
                ],
            )

    ref_knl = knl

    knl = lp.add_prefetch(knl, "a", sweep_inames=["i", "j"])
    knl = lp.join_inames(knl, ["a_dim_0", "a_dim_1"])

    kernel_gen = lp.generate_loop_schedules(knl)
    kernel_gen = lp.check_kernels(kernel_gen)

    lp.auto_test_vs_ref(ref_knl, ctx, kernel_gen)





def test_divisibility_assumption(ctx_factory):
    ctx = ctx_factory()

    knl = lp.make_kernel(ctx.devices[0],
            "[n] -> {[i]: 0<=i<n}",
            [
                "b[i] = 2*a[i]"
                ],
            [
                lp.GlobalArg("a", np.float32, shape=("n",)),
                lp.GlobalArg("b", np.float32, shape=("n",)),
                lp.ValueArg("n", np.int32),
                ],
            assumptions="n>=1 and (exists zz: n = 16*zz)")

    ref_knl = knl

    knl = lp.split_iname(knl, "i", 16)

    for k in lp.generate_loop_schedules(knl):
        code = lp.generate_code(k)
        assert "if" not in code

    kernel_gen = lp.generate_loop_schedules(knl)
    kernel_gen = lp.check_kernels(kernel_gen)

    lp.auto_test_vs_ref(ref_knl, ctx, kernel_gen,
            parameters={"n": 16**3})




def test_multi_cse(ctx_factory):
    ctx = ctx_factory()

    knl = lp.make_kernel(ctx.devices[0],
            "{[i]: 0<=i<100}",
            [
                "[i] <float32> z[i] = a[i] + a[i]**2"
                ],
            [lp.GlobalArg("a", np.float32, shape=(100,))],
            local_sizes={0: 16})

    knl = lp.split_iname(knl, "i", 16, inner_tag="l.0")
    knl = lp.add_prefetch(knl, "a", [])

    kernel_gen = lp.generate_loop_schedules(knl)
    kernel_gen = lp.check_kernels(kernel_gen)

    for gen_knl in kernel_gen:
        compiled = lp.CompiledKernel(ctx, gen_knl)
        print compiled.code





def test_stencil(ctx_factory):
    ctx = ctx_factory()

    # n=32 causes corner case behavior in size calculations for temprorary (a
    # non-unifiable, two-constant-segments PwAff as the base index)

    n = 256
    knl = lp.make_kernel(ctx.devices[0],
            "{[i,j]: 0<= i,j < %d}" % n,
            [
                "a_offset(ii, jj) := a[ii+1, jj+1]",
                "z[i,j] = -2*a_offset(i,j)"
                    " + a_offset(i,j-1)"
                    " + a_offset(i,j+1)"
                    " + a_offset(i-1,j)"
                    " + a_offset(i+1,j)"
                ],
            [
                lp.GlobalArg("a", np.float32, shape=(n+2,n+2,)),
                lp.GlobalArg("z", np.float32, shape=(n+2,n+2,))
                ])

    ref_knl = knl

    def variant_1(knl):
        knl = lp.split_iname(knl, "i", 16, outer_tag="g.1", inner_tag="l.1")
        knl = lp.split_iname(knl, "j", 16, outer_tag="g.0", inner_tag="l.0")
        knl = lp.add_prefetch(knl, "a", ["i_inner", "j_inner"])
        return knl

    for variant in [variant_1]:
        kernel_gen = lp.generate_loop_schedules(variant(knl),
                loop_priority=["i_outer", "i_inner_0", "j_0"])
        kernel_gen = lp.check_kernels(kernel_gen)

        lp.auto_test_vs_ref(ref_knl, ctx, kernel_gen,
                fills_entire_output=False, print_ref_code=True,
                op_count=[n*n], op_label=["cells"])




def test_eq_constraint(ctx_factory):
    ctx = ctx_factory()

    knl = lp.make_kernel(ctx.devices[0],
            "{[i,j]: 0<= i,j < 32}",
            [
                "a[i] = b[i]"
                ],
            [
                lp.GlobalArg("a", np.float32, shape=(1000,)),
                lp.GlobalArg("b", np.float32, shape=(1000,))
                ])

    knl = lp.split_iname(knl, "i", 16, outer_tag="g.0")
    knl = lp.split_iname(knl, "i_inner", 16, outer_tag=None, inner_tag="l.0")

    kernel_gen = lp.generate_loop_schedules(knl)
    kernel_gen = lp.check_kernels(kernel_gen)

    for knl in kernel_gen:
        print lp.generate_code(knl)




def test_argmax(ctx_factory):
    dtype = np.dtype(np.float32)
    ctx = ctx_factory()
    queue = cl.CommandQueue(ctx)
    order = "C"

    n = 10000

    knl = lp.make_kernel(ctx.devices[0],
            "{[i]: 0<=i<%d}" % n,
            [
                "<> result = argmax(i, fabs(a[i]))",
                "max_idx = result.index",
                "max_val = result.value",
                ],
            [
                lp.GlobalArg("a", dtype, shape=(n,), order=order),
                lp.GlobalArg("max_idx", np.int32, shape=(), order=order),
                lp.GlobalArg("max_val", dtype, shape=(), order=order),
                ])

    a = np.random.randn(10000).astype(dtype)
    cknl = lp.CompiledKernel(ctx, knl)
    evt, (max_idx, max_val) = cknl(queue, a=a, out_host=True)
    assert max_val == np.max(np.abs(a))
    assert max_idx == np.where(np.abs(a)==max_val)[-1]




def make_random_value():
    from random import randrange, uniform
    v = randrange(3)
    if v == 0:
        while True:
            z = randrange(-1000, 1000)
            if z:
                return z

    elif v == 1:
        return uniform(-10, 10)
    else:
        cval = uniform(-10, 10) + 1j*uniform(-10, 10)
        if randrange(0, 2) == 0:
            return np.complex128(cval)
        else:
            return np.complex128(cval)




def make_random_expression(var_values, size):
    from random import randrange
    import pymbolic.primitives as p
    v = randrange(1500)
    size[0] += 1
    if v < 500 and size[0] < 40:
        term_count = randrange(2, 5)
        if randrange(2) < 1:
            cls = p.Sum
        else:
            cls = p.Product
        return cls(tuple(
            make_random_expression(var_values, size)
            for i in range(term_count)))
    elif v < 750:
        return make_random_value()
    elif v < 1000:
        var_name = "var_%d" % len(var_values)
        assert var_name not in var_values
        var_values[var_name] = make_random_value()
        return p.Variable(var_name)
    elif v < 1250:
        # Cannot use '-' because that destroys numpy constants.
        return p.Sum((
            make_random_expression(var_values, size),
            - make_random_expression(var_values, size)))
    elif v < 1500:
        # Cannot use '/' because that destroys numpy constants.
        return p.Quotient(
                make_random_expression(var_values, size),
                make_random_expression(var_values, size))


def generate_random_fuzz_examples(count):
    for i in xrange(count):
        size = [0]
        var_values = {}
        expr = make_random_expression(var_values, size)
        yield expr, var_values

def test_fuzz_code_generator(ctx_factory):
    ctx = ctx_factory()
    queue = cl.CommandQueue(ctx)

    #from expr_fuzz import get_fuzz_examples
    for expr, var_values in generate_random_fuzz_examples(50):
    #for expr, var_values in get_fuzz_examples():
        from pymbolic import evaluate
        true_value = evaluate(expr, var_values)

        def get_dtype(x):
            if isinstance(x, (complex, np.complexfloating)):
                return np.complex128
            else:
                return np.float64

        knl = lp.make_kernel(ctx.devices[0], "{ : }",
                [lp.Instruction(None, "value", expr)],
                [lp.GlobalArg("value", np.complex128, shape=())]
                + [
                    lp.ValueArg(name, get_dtype(val))
                    for name, val in var_values.iteritems()
                    ])
        ck = lp.CompiledKernel(ctx, knl)
        evt, (lp_value,) = ck(queue, out_host=True, **var_values)
        err = abs(true_value-lp_value)/abs(true_value)
        if abs(err) > 1e-10:
            print "---------------------------------------------------------------------"
            print "WRONG: rel error=%g" % err
            print "true=%r" % true_value
            print "loopy=%r" % lp_value
            print "---------------------------------------------------------------------"
            print ck.code
            print "---------------------------------------------------------------------"
            print var_values
            print "---------------------------------------------------------------------"
            print repr(expr)
            print "---------------------------------------------------------------------"
            print expr
            print "---------------------------------------------------------------------"
            1/0






def test_empty_reduction(ctx_factory):
    dtype = np.dtype(np.float32)
    ctx = ctx_factory()
    queue = cl.CommandQueue(ctx)

    knl = lp.make_kernel(ctx.devices[0],
            [
                "{[i]: 0<=i<20}",
                "[i] -> {[j]: 0<=j<0}"
                ],
            [
                "a[i] = sum(j, j)",
                ],
            [
                lp.GlobalArg("a", dtype, (20,)),
                ])
    cknl = lp.CompiledKernel(ctx, knl)

    evt, (a,) = cknl(queue)

    assert (a.get() == 0).all()





def test_nested_dependent_reduction(ctx_factory):
    dtype = np.dtype(np.int32)
    ctx = ctx_factory()
    queue = cl.CommandQueue(ctx)

    knl = lp.make_kernel(ctx.devices[0],
            [
                "{[i]: 0<=i<n}",
                "{[j]: 0<=j<i+sumlen}"
                ],
            [
                "<> sumlen = l[i]",
                "a[i] = sum(j, j)",
                ],
            [
                lp.ValueArg("n", np.int32),
                lp.GlobalArg("a", dtype, ("n",)),
                lp.GlobalArg("l", np.int32, ("n",)),
                ])

    cknl = lp.CompiledKernel(ctx, knl)

    n = 330
    l = np.arange(n, dtype=np.int32)
    evt, (a,) = cknl(queue, l=l, n=n, out_host=True)

    tgt_result = (2*l-1)*2*l/2
    assert (a == tgt_result).all()







def test_dependent_loop_bounds(ctx_factory):
    dtype = np.dtype(np.float32)
    ctx = ctx_factory()

    knl = lp.make_kernel(ctx.devices[0],
            [
                "{[i]: 0<=i<n}",
                "{[jj]: 0<=jj<row_len}",
                ],
            [
                "<> row_len = a_rowstarts[i+1] - a_rowstarts[i]",
                "ax[i] = sum(jj, a_values[a_rowstarts[i]+jj])",
                ],
            [
                lp.GlobalArg("a_rowstarts", np.int32),
                lp.GlobalArg("a_indices", np.int32),
                lp.GlobalArg("a_values", dtype),
                lp.GlobalArg("x", dtype),
                lp.GlobalArg("ax", dtype),
                lp.ValueArg("n", np.int32),
                ],
            assumptions="n>=1 and row_len>=1")

    cknl = lp.CompiledKernel(ctx, knl)
    print "---------------------------------------------------"
    cknl.print_code()
    print "---------------------------------------------------"




def test_dependent_loop_bounds_2(ctx_factory):
    dtype = np.dtype(np.float32)
    ctx = ctx_factory()

    knl = lp.make_kernel(ctx.devices[0],
            [
                "{[i]: 0<=i<n}",
                "{[jj]: 0<=jj<row_len}",
                ],
            [
                "<> row_start = a_rowstarts[i]",
                "<> row_len = a_rowstarts[i+1] - row_start",
                "ax[i] = sum(jj, a_values[row_start+jj])",
                ],
            [
                lp.GlobalArg("a_rowstarts", np.int32),
                lp.GlobalArg("a_indices", np.int32),
                lp.GlobalArg("a_values", dtype),
                lp.GlobalArg("x", dtype),
                lp.GlobalArg("ax", dtype),
                lp.ValueArg("n", np.int32),
                ],
            assumptions="n>=1 and row_len>=1")

    knl = lp.split_iname(knl, "i", 128, outer_tag="g.0",
            inner_tag="l.0")
    cknl = lp.CompiledKernel(ctx, knl)
    print "---------------------------------------------------"
    cknl.print_code()
    print "---------------------------------------------------"





def test_dependent_loop_bounds_3(ctx_factory):
    # The point of this test is that it shows a dependency between
    # domains that is exclusively mediated by the row_len temporary.
    # It also makes sure that row_len gets read before any
    # conditionals use it.

    dtype = np.dtype(np.float32)
    ctx = ctx_factory()

    knl = lp.make_kernel(ctx.devices[0],
            [
                "{[i]: 0<=i<n}",
                "{[jj]: 0<=jj<row_len}",
                ],
            [
                "<> row_len = a_row_lengths[i]",
                "a[i,jj] = 1",
                ],
            [
                lp.GlobalArg("a_row_lengths", np.int32),
                lp.GlobalArg("a", dtype, shape=("n,n"), order="C"),
                lp.ValueArg("n", np.int32),
                ])

    assert knl.parents_per_domain()[1] == 0

    knl = lp.split_iname(knl, "i", 128, outer_tag="g.0",
            inner_tag="l.0")

    cknl = lp.CompiledKernel(ctx, knl)
    print "---------------------------------------------------"
    cknl.print_code()
    print "---------------------------------------------------"

    knl_bad = lp.split_iname(knl, "jj", 128, outer_tag="g.1",
            inner_tag="l.1")

    import pytest
    with pytest.raises(RuntimeError):
        list(lp.generate_loop_schedules(knl_bad))




def test_independent_multi_domain(ctx_factory):
    dtype = np.dtype(np.float32)
    ctx = ctx_factory()
    queue = cl.CommandQueue(ctx)

    knl = lp.make_kernel(ctx.devices[0],
            [
                "{[i]: 0<=i<n}",
                "{[j]: 0<=j<n}",
                ],
            [
                "a[i] = 1",
                "b[j] = 2",
                ],
            [
                lp.GlobalArg("a", dtype, shape=("n"), order="C"),
                lp.GlobalArg("b", dtype, shape=("n"), order="C"),
                lp.ValueArg("n", np.int32),
                ])


    knl = lp.split_iname(knl, "i", 16, outer_tag="g.0",
            inner_tag="l.0")
    knl = lp.split_iname(knl, "j", 16, outer_tag="g.0",
            inner_tag="l.0")
    assert knl.parents_per_domain() == 2*[None]

    n = 50
    cknl = lp.CompiledKernel(ctx, knl)
    evt, (a, b) = cknl(queue, n=n, out_host=True)

    assert a.shape == (50,)
    assert b.shape == (50,)
    assert (a == 1).all()
    assert (b == 2).all()





def test_bare_data_dependency(ctx_factory):
    dtype = np.dtype(np.float32)
    ctx = ctx_factory()
    queue = cl.CommandQueue(ctx)

    knl = lp.make_kernel(ctx.devices[0],
            [
                "[znirp] -> {[i]: 0<=i<znirp}",
                ],
            [
                "<> znirp = n",
                "a[i] = 1",
                ],
            [
                lp.GlobalArg("a", dtype, shape=("n"), order="C"),
                lp.ValueArg("n", np.int32),
                ])

    cknl = lp.CompiledKernel(ctx, knl)
    n = 20000
    evt, (a,) = cknl(queue, n=n, out_host=True)

    assert a.shape == (n,)
    assert (a == 1).all()




def test_equality_constraints(ctx_factory):
    dtype = np.float32
    ctx = ctx_factory()

    order = "C"

    n = 10

    knl = lp.make_kernel(ctx.devices[0], [
            "[n] -> {[i,j]: 0<=i,j<n }",
            "{[k]: k =i+5 and k < n}",
            ],
            [
                "a[i,j] = 5 {id=set_all}",
                "a[i,k] = 22 {dep=set_all}",
                ],
            [
                lp.GlobalArg("a", dtype, shape="n, n", order=order),
                lp.ValueArg("n", np.int32, approximately=1000),
                ],
            name="equality_constraints", assumptions="n>=1")

    seq_knl = knl

    knl = lp.split_iname(knl, "i", 16, outer_tag="g.0", inner_tag="l.0")
    knl = lp.split_iname(knl, "j", 16, outer_tag="g.1", inner_tag="l.1")
    #print knl
    #print knl.domains[0].detect_equalities()

    kernel_gen = lp.generate_loop_schedules(knl)
    kernel_gen = lp.check_kernels(kernel_gen, dict(n=n))

    lp.auto_test_vs_ref(seq_knl, ctx, kernel_gen,
            parameters=dict(n=n), print_ref_code=True)




def test_stride(ctx_factory):
    dtype = np.float32
    ctx = ctx_factory()

    order = "C"

    n = 10

    knl = lp.make_kernel(ctx.devices[0], [
            "{[i]: 0<=i<n and (exists l: i = 2*l)}",
            ],
            [
                "a[i] = 5",
                ],
            [
                lp.GlobalArg("a", dtype, shape="n", order=order),
                lp.ValueArg("n", np.int32, approximately=1000),
                ],
            assumptions="n>=1")

    seq_knl = knl

    kernel_gen = lp.generate_loop_schedules(knl)
    kernel_gen = lp.check_kernels(kernel_gen, dict(n=n))

    lp.auto_test_vs_ref(seq_knl, ctx, kernel_gen,
            parameters=dict(n=n), fills_entire_output=False)




def test_domain_dependency_via_existentially_quantified_variable(ctx_factory):
    dtype = np.float32
    ctx = ctx_factory()

    order = "C"

    n = 10

    knl = lp.make_kernel(ctx.devices[0], [
            "{[i]: 0<=i<n }",
            "{[k]: k=i and (exists l: k = 2*l) }",
            ],
            [
                "a[i] = 5 {id=set}",
                "a[k] = 6 {dep=set}",
                ],
            [
                lp.GlobalArg("a", dtype, shape="n", order=order),
                lp.ValueArg("n", np.int32, approximately=1000),
                ],
            assumptions="n>=1")

    seq_knl = knl

    kernel_gen = lp.generate_loop_schedules(knl)
    kernel_gen = lp.check_kernels(kernel_gen, dict(n=n))

    lp.auto_test_vs_ref(seq_knl, ctx, kernel_gen,
            parameters=dict(n=n), )




# {{{ test race detection

def test_ilp_write_race_detection_global(ctx_factory):
    ctx = ctx_factory()

    knl = lp.make_kernel(ctx.devices[0], [
            "[n] -> {[i,j]: 0<=i,j<n }",
            ],
            [
                "[j:ilp] a[i] = 5+i+j",
                ],
            [
                lp.GlobalArg("a", np.float32),
                lp.ValueArg("n", np.int32, approximately=1000),
                ],
            assumptions="n>=1")

    from loopy.check import WriteRaceConditionError
    import pytest
    with pytest.raises(WriteRaceConditionError):
        list(lp.generate_loop_schedules(knl))




def test_ilp_write_race_avoidance_local(ctx_factory):
    ctx = ctx_factory()

    knl = lp.make_kernel(ctx.devices[0],
            "{[i,j]: 0<=i<16 and 0<=j<17 }",
            [
                "[i:l.0, j:ilp] <> a[i] = 5+i+j",
                ],
            [])

    for k in lp.generate_loop_schedules(knl):
        assert k.temporary_variables["a"].shape == (16,17)




def test_ilp_write_race_avoidance_private(ctx_factory):
    ctx = ctx_factory()

    knl = lp.make_kernel(ctx.devices[0],
            "{[j]: 0<=j<16 }",
            [
                "[j:ilp] <> a = 5+j",
                ],
            [])

    for k in lp.generate_loop_schedules(knl):
        assert k.temporary_variables["a"].shape == (16,)

# }}}




if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        exec(sys.argv[1])
    else:
        from py.test.cmdline import main
        main([__file__])

# vim: foldmethod=marker
