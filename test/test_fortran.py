from __future__ import division, absolute_import

__copyright__ = "Copyright (C) 2015 Andreas Kloeckner"

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


import sys
import numpy as np
import loopy as lp
import pyopencl as cl
import pyopencl.clrandom  # noqa
import pytest

import logging
logger = logging.getLogger(__name__)

from pyopencl.tools import pytest_generate_tests_for_pyopencl \
        as pytest_generate_tests

__all__ = [
        "pytest_generate_tests",
        "cl"  # 'cl.create_some_context'
        ]


pytestmark = pytest.mark.importorskip("fparser")


def test_fill(ctx_factory):
    fortran_src = """
        subroutine fill(out, a, n)
          implicit none

          real*8 a, out(n)
          integer n

          do i = 1, n
            out(i) = a
          end do
        end

        !$loopy begin transform
        !
        ! fill = lp.split_iname(fill, "i", 128,
        !     outer_tag="g.0", inner_tag="l.0")
        !
        !$loopy end transform
        """

    from loopy.frontend.fortran import f2loopy
    knl, = f2loopy(fortran_src)

    ctx = ctx_factory()

    lp.auto_test_vs_ref(knl, ctx, knl, parameters=dict(n=5, a=5))


def test_fill_const(ctx_factory):
    fortran_src = """
        subroutine fill(out, a, n)
          implicit none

          real*8 a, out(n)
          integer n

          do i = 1, n
            out(i) = 3.45
          end do
        end
        """

    from loopy.frontend.fortran import f2loopy
    knl, = f2loopy(fortran_src)

    ctx = ctx_factory()

    lp.auto_test_vs_ref(knl, ctx, knl, parameters=dict(n=5, a=5))


def test_asterisk_in_shape(ctx_factory):
    fortran_src = """
        subroutine fill(out, out2, inp, n)
          implicit none

          real*8 a, out(n), out2(n), inp(*)
          integer n

          do i = 1, n
            a = inp(n)
            out(i) = 5*a
            out2(i) = 6*a
          end do
        end
        """

    from loopy.frontend.fortran import f2loopy
    knl, = f2loopy(fortran_src)

    ctx = ctx_factory()
    queue = cl.CommandQueue(ctx)

    knl(queue, inp=np.array([1, 2, 3.]), n=3)


def test_temporary_to_subst(ctx_factory):
    fortran_src = """
        subroutine fill(out, out2, inp, n)
          implicit none

          real*8 a, out(n), out2(n), inp(n)
          integer n

          do i = 1, n
            a = inp(n)
            out(i) = 5*a
            out2(i) = 6*a
          end do
        end
        """

    from loopy.frontend.fortran import f2loopy
    knl, = f2loopy(fortran_src)

    ref_knl = knl

    knl = lp.temporary_to_subst(knl, "a")

    ctx = ctx_factory()
    lp.auto_test_vs_ref(ref_knl, ctx, knl, parameters=dict(n=5))


def test_temporary_to_subst_two_defs(ctx_factory):
    fortran_src = """
        subroutine fill(out, out2, inp, n)
          implicit none

          real*8 a, out(n), out2(n), inp(n)
          integer n

          do i = 1, n
            a = inp(i)
            out(i) = 5*a
            a = 3*inp(n)
            out2(i) = 6*a
          end do
        end
        """

    from loopy.frontend.fortran import f2loopy
    knl, = f2loopy(fortran_src)

    ref_knl = knl

    knl = lp.temporary_to_subst(knl, "a")

    ctx = ctx_factory()
    lp.auto_test_vs_ref(ref_knl, ctx, knl, parameters=dict(n=5))


def test_temporary_to_subst_indices(ctx_factory):
    fortran_src = """
        subroutine fill(out, out2, inp, n)
          implicit none

          real*8 a(n), out(n), out2(n), inp(n)
          integer n

          do i = 1, n
            a(i) = 6*inp(i)
          enddo

          do i = 1, n
            out(i) = 5*a(i)
          end do
        end
        """

    from loopy.frontend.fortran import f2loopy
    knl, = f2loopy(fortran_src)

    knl = lp.fix_parameters(knl, n=5)

    ref_knl = knl

    assert "a" in knl.temporary_variables
    knl = lp.temporary_to_subst(knl, "a")
    assert "a" not in knl.temporary_variables

    ctx = ctx_factory()
    lp.auto_test_vs_ref(ref_knl, ctx, knl)


def test_if(ctx_factory):
    fortran_src = """
        subroutine fill(out, out2, inp, n)
          implicit none

          real*8 a, b, out(n), out2(n), inp(n)
          integer n

          do i = 1, n
            a = inp(i)
            if (a.ge.3) then
                b = 2*a
                do j = 1,3
                    b = 3 * b
                end do
                out(i) = 5*b
            else
                out(i) = 4*a
            endif
          end do
        end
        """

    from loopy.frontend.fortran import f2loopy
    knl, = f2loopy(fortran_src)

    ref_knl = knl

    knl = lp.temporary_to_subst(knl, "a")

    ctx = ctx_factory()
    lp.auto_test_vs_ref(ref_knl, ctx, knl, parameters=dict(n=5))


def test_tagged(ctx_factory):
    fortran_src = """
        subroutine rot_norm(out, alpha, out2, inp, inp2, n)
          implicit none
          real*8 a, b, r, out(n), out2(n), inp(n), inp2(n)
          real*8 alpha
          integer n

          do i = 1, n
            !$loopy begin tagged: input
            a = cos(alpha)*inp(i) + sin(alpha)*inp2(i)
            b = -sin(alpha)*inp(i) + cos(alpha)*inp2(i)
            !$loopy end tagged: input

            r = sqrt(a**2 + b**2)
            a = a/r
            b = b/r

            out(i) = a
            out2(i) = b
          end do
        end
        """

    from loopy.frontend.fortran import f2loopy
    knl, = f2loopy(fortran_src)

    assert sum(1 for insn in lp.find_instructions(knl, "*$input")) == 2


@pytest.mark.parametrize("buffer_inames", [
    "",
    "i_inner",
    "i_inner,j_inner",
    ])
def test_matmul(ctx_factory, buffer_inames):
    fortran_src = """
        subroutine dgemm(m,n,l,a,b,c)
          implicit none
          real*8 temp, a(m,l),b(l,n),c(m,n)
          integer m,n,k,i,j,l

          do j = 1,n
            do i = 1,m
              do k = 1,l
                c(i,j) = c(i,j) + b(k,j)*a(i,k)
              end do
            end do
          end do
        end subroutine
        """

    from loopy.frontend.fortran import f2loopy
    knl, = f2loopy(fortran_src)

    assert len(knl.domains) == 1

    ref_knl = knl

    knl = lp.split_iname(knl, "i", 16,
            outer_tag="g.0", inner_tag="l.1")
    knl = lp.split_iname(knl, "j", 8,
            outer_tag="g.1", inner_tag="l.0")
    knl = lp.split_iname(knl, "k", 32)
    knl = lp.assume(knl, "n mod 32 = 0")
    knl = lp.assume(knl, "m mod 32 = 0")
    knl = lp.assume(knl, "l mod 16 = 0")

    knl = lp.extract_subst(knl, "a_acc", "a[i1,i2]", parameters="i1, i2")
    knl = lp.extract_subst(knl, "b_acc", "b[i1,i2]", parameters="i1, i2")
    knl = lp.precompute(knl, "a_acc", "k_inner,i_inner")
    knl = lp.precompute(knl, "b_acc", "j_inner,k_inner")

    knl = lp.buffer_array(knl, "c", buffer_inames=buffer_inames,
            init_expression="0", store_expression="base+buffer")

    #ctx = ctx_factory()
    #lp.auto_test_vs_ref(ref_knl, ctx, knl, parameters=dict(n=5, m=7, l=10))

    # FIXME: Make r/w tests possible, reactivate the above
    knl = lp.preprocess_kernel(knl)
    for k in lp.generate_loop_schedules(knl):
        code, _ = lp.generate_code(k)
        print(code)


@pytest.mark.xfail
def test_batched_sparse():
    fortran_src = """
        subroutine sparse(rowstarts, colindices, values, m, n, nvecs, nvals, x, y)
          implicit none

          integer rowstarts(m+1), colindices(nvals)
          real*8 values(nvals)
          real*8 x(n, nvecs), y(n, nvecs), rowsum(nvecs)

          integer m, n, rowstart, rowend, length, nvals, nvecs

          do i = 1, m
            rowstart = rowstarts(i)
            rowend = rowstarts(i+1)
            length = rowend - rowstart

            do k = 1, nvecs
              rowsum(k) = 0
            enddo
            do k = 1, nvecs
              do j = 1, length
                rowsum(k) = rowsum(k) + &
                  x(colindices(rowstart+j-1),k)*values(rowstart+j-1)
              end do
            end do
            do k = 1, nvecs
              y(i,k) = rowsum(k)
            end do
          end do
        end

        """

    from loopy.frontend.fortran import f2loopy
    knl, = f2loopy(fortran_src)

    knl = lp.split_iname(knl, "i", 128)
    knl = lp.tag_inames(knl, {"i_outer": "g.0"})
    knl = lp.tag_inames(knl, {"i_inner": "l.0"})
    knl = lp.add_prefetch(knl, "values")
    knl = lp.add_prefetch(knl, "colindices")
    knl = lp.fix_parameters(knl, nvecs=4)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        exec(sys.argv[1])
    else:
        from py.test.cmdline import main
        main([__file__])

# vim: foldmethod=marker