from __future__ import division, absolute_import, print_function

__copyright__ = "Copyright (C) 2015 James Stevens"

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

import six

import loopy as lp
import warnings
from islpy import dim_type
import islpy as isl
from pymbolic.mapper import CombineMapper
from functools import reduce


class ToCountMap:
    """Maps any type of key to an arithmetic type."""

    def __init__(self, init_dict=None):
        if init_dict is None:
            init_dict = {}

        self.dict = init_dict

    def __add__(self, other):
        result = self.dict.copy()

        for k, v in six.iteritems(other.dict):
            result[k] = self.dict.get(k, 0) + v

        return ToCountMap(result)

    def __radd__(self, other):
        if other != 0:
            raise ValueError("ToCountMap: Attempted to add ToCountMap "
                                "to {} {}. ToCountMap may only be added to "
                                "0 and other ToCountMap objects."
                                .format(type(other), other))

        return self

    def __mul__(self, other):
        if isinstance(other, isl.PwQPolynomial):
            return ToCountMap(dict(
                (index, self.dict[index]*other)
                for index in self.dict.keys()))
        else:
            raise ValueError("ToCountMap: Attempted to multiply "
                                "ToCountMap by {} {}."
                                .format(type(other), other))

    __rmul__ = __mul__

    def __getitem__(self, index):
        try:
            return self.dict[index]
        except KeyError:
            return isl.PwQPolynomial('{ 0 }')

    def __repr__(self):
        return repr(self.dict)


def stringify_stats_mapping(m):
    result = ""
    for key in sorted(m.keys(), key=lambda k: str(k)):
        result += ("%s : %s\n" % (key, m[key]))
    return result


class ExpressionOpCounter(CombineMapper):

    def __init__(self, knl):
        self.knl = knl
        from loopy.expression import TypeInferenceMapper
        self.type_inf = TypeInferenceMapper(knl)

    def combine(self, values):
        return sum(values)

    def map_constant(self, expr):
        return ToCountMap()

    map_tagged_variable = map_constant
    map_variable = map_constant

    #def map_wildcard(self, expr):
    #    return 0,0

    #def map_function_symbol(self, expr):
    #    return 0,0

    map_call = map_constant

    # def map_call_with_kwargs(self, expr):  # implemented in CombineMapper

    def map_subscript(self, expr):  # implemented in CombineMapper
        return self.rec(expr.index)

    # def map_lookup(self, expr):  # implemented in CombineMapper

    def map_sum(self, expr):
        if expr.children:
            return ToCountMap(
                        {self.type_inf(expr): len(expr.children)-1}
                        ) + sum(self.rec(child) for child in expr.children)
        else:
            return ToCountMap()

    def map_product(self, expr):
        from pymbolic.primitives import is_zero
        if expr.children:
            return sum(ToCountMap({self.type_inf(expr): 1}) + self.rec(child)
                       for child in expr.children
                       # Do not count '(-1)* ' (as produced by
                       # subtraction in pymbolic): Assume this
                       # gets implemented as a sign flip or
                       # as subtraction. (Confirmed to be true on
                       # at least Nvidia 352.30.)
                       if not is_zero(child + 1)) + \
                       ToCountMap({self.type_inf(expr): -1})
        else:
            return ToCountMap()

    def map_quotient(self, expr, *args):
        return ToCountMap({self.type_inf(expr): 1}) \
                                + self.rec(expr.numerator) \
                                + self.rec(expr.denominator)

    map_floor_div = map_quotient
    map_remainder = map_quotient  # implemented in CombineMapper

    def map_power(self, expr):
        return ToCountMap({self.type_inf(expr): 1}) \
                                + self.rec(expr.base) \
                                + self.rec(expr.exponent)

    def map_left_shift(self, expr):  # implemented in CombineMapper
        return ToCountMap({self.type_inf(expr): 1}) \
                                + self.rec(expr.shiftee) \
                                + self.rec(expr.shift)

    map_right_shift = map_left_shift

    def map_bitwise_not(self, expr):  # implemented in CombineMapper
        return ToCountMap({self.type_inf(expr): 1}) \
                                + self.rec(expr.child)

    def map_bitwise_or(self, expr):
        # implemented in CombineMapper, maps to map_sum;
        return ToCountMap(
                        {self.type_inf(expr): len(expr.children)-1}
                        ) + sum(self.rec(child) for child in expr.children)

    map_bitwise_xor = map_bitwise_or
    # implemented in CombineMapper, maps to map_sum;

    map_bitwise_and = map_bitwise_or
    # implemented in CombineMapper, maps to map_sum;

    def map_comparison(self, expr):  # implemented in CombineMapper
        return self.rec(expr.left)+self.rec(expr.right)

    def map_logical_not(self, expr):
        return self.rec(expr.child)

    def map_logical_or(self, expr):
        return sum(self.rec(child) for child in expr.children)

    map_logical_and = map_logical_or

    def map_if(self, expr):  # implemented in CombineMapper, recurses
        warnings.warn("ExpressionOpCounter counting DRAM accesses as "
                      "sum of if-statement branches.")
        return self.rec(expr.condition) + self.rec(expr.then) + self.rec(expr.else_)

    def map_if_positive(self, expr):  # implemented in FlopCounter
        warnings.warn("ExpressionOpCounter counting DRAM accesses as "
                      "sum of if_pos-statement branches.")
        return self.rec(expr.criterion) + self.rec(expr.then) + self.rec(expr.else_)

    map_min = map_bitwise_or
    # implemented in CombineMapper, maps to map_sum;  # TODO test

    map_max = map_min  # implemented in CombineMapper, maps to map_sum;  # TODO test

    def map_common_subexpression(self, expr):
        raise NotImplementedError("ExpressionOpCounter encountered "
                                  "common_subexpression, "
                                  "map_common_subexpression not implemented.")

    def map_substitution(self, expr):
        raise NotImplementedError("ExpressionOpCounter encountered substitution, "
                                  "map_substitution not implemented.")

    def map_derivative(self, expr):
        raise NotImplementedError("ExpressionOpCounter encountered derivative, "
                                  "map_derivative not implemented.")

    def map_slice(self, expr):
        raise NotImplementedError("ExpressionOpCounter encountered slice, "
                                  "map_slice not implemented.")


class GlobalSubscriptCounter(CombineMapper):

    def __init__(self, knl):
        self.knl = knl
        from loopy.expression import TypeInferenceMapper
        self.type_inf = TypeInferenceMapper(knl)

    def combine(self, values):
        return sum(values)

    def map_constant(self, expr):
        return ToCountMap()

    map_tagged_variable = map_constant
    map_variable = map_constant
    map_call = map_constant

    def map_subscript(self, expr):
        name = expr.aggregate.name  # name of array

        if name in self.knl.arg_dict:
            array = self.knl.arg_dict[name]
        else:
            # this is a temporary variable
            return self.rec(expr.index)

        if not isinstance(array, lp.GlobalArg):
            # this array is not in global memory
            return self.rec(expr.index)

        index = expr.index  # could be tuple or scalar index
        if not isinstance(index, tuple):
            index = (index,)

        from loopy.symbolic import get_dependencies
        from loopy.kernel.data import LocalIndexTag
        my_inames = get_dependencies(index) & self.knl.all_inames()
        local_id0 = None
        local_id_found = False
        for iname in my_inames:
            # find local id0
            tag = self.knl.iname_to_tag.get(iname)
            if isinstance(tag, LocalIndexTag):
                local_id_found = True
                if tag.axis == 0:
                    local_id0 = iname
                    break  # there will be only one local_id0

        if not local_id_found:
            # count as uniform access
            return ToCountMap(
                    {(self.type_inf(expr), 'uniform'): 1}
                    ) + self.rec(expr.index)

        if local_id0 is None:
            # only non-zero local id(s) found, assume non-consecutive access
            return ToCountMap(
                    {(self.type_inf(expr), 'nonconsecutive'): 1}
                    ) + self.rec(expr.index)

        # check coefficient of local_id0 for each axis
        from loopy.symbolic import CoefficientCollector
        from pymbolic.primitives import Variable
        for idx, axis_tag in zip(index, array.dim_tags):

            coeffs = CoefficientCollector()(idx)
            # check if he contains the lid 0 guy
            try:
                coeff_id0 = coeffs[Variable(local_id0)]
            except KeyError:
                # does not contain local_id0
                continue

            if coeff_id0 != 1:
                # non-consecutive access
                return ToCountMap(
                        {(self.type_inf(expr), 'nonconsecutive'): 1}
                        ) + self.rec(expr.index)

            # coefficient is 1, now determine if stride is 1
            from loopy.kernel.array import FixedStrideArrayDimTag
            if isinstance(axis_tag, FixedStrideArrayDimTag):
                stride = axis_tag.stride
            else:
                continue

            if stride != 1:
                # non-consecutive
                return ToCountMap(
                        {(self.type_inf(expr), 'nonconsecutive'): 1}
                        ) + self.rec(expr.index)

            # else, stride == 1, continue since another idx could contain id0

        # loop finished without returning, stride==1 for every instance of local_id0
        return ToCountMap(
                {(self.type_inf(expr), 'consecutive'): 1}
                ) + self.rec(expr.index)

    def map_sum(self, expr):
        if expr.children:
            return sum(self.rec(child) for child in expr.children)
        else:
            return ToCountMap()

    map_product = map_sum

    def map_quotient(self, expr, *args):
        return self.rec(expr.numerator) + self.rec(expr.denominator)

    map_floor_div = map_quotient
    map_remainder = map_quotient

    def map_power(self, expr):
        return self.rec(expr.base) + self.rec(expr.exponent)

    def map_left_shift(self, expr):
        return self.rec(expr.shiftee)+self.rec(expr.shift)

    map_right_shift = map_left_shift

    def map_bitwise_not(self, expr):
        return self.rec(expr.child)

    def map_bitwise_or(self, expr):
        return sum(self.rec(child) for child in expr.children)

    map_bitwise_xor = map_bitwise_or
    map_bitwise_and = map_bitwise_or

    def map_comparison(self, expr):
        return self.rec(expr.left)+self.rec(expr.right)

    map_logical_not = map_bitwise_not
    map_logical_or = map_bitwise_or
    map_logical_and = map_logical_or

    def map_if(self, expr):
        warnings.warn("GlobalSubscriptCounter counting DRAM accesses as "
                      "sum of if-statement branches.")
        return self.rec(expr.condition) + self.rec(expr.then) + self.rec(expr.else_)

    def map_if_positive(self, expr):
        warnings.warn("GlobalSubscriptCounter counting DRAM accesses as "
                      "sum of if_pos-statement branches.")
        return self.rec(expr.criterion) + self.rec(expr.then) + self.rec(expr.else_)

    map_min = map_bitwise_or
    map_max = map_min

    def map_common_subexpression(self, expr):
        raise NotImplementedError("GlobalSubscriptCounter encountered "
                                  "common_subexpression, "
                                  "map_common_subexpression not implemented.")

    def map_substitution(self, expr):
        raise NotImplementedError("GlobalSubscriptCounter encountered "
                                  "substitution, "
                                  "map_substitution not implemented.")

    def map_derivative(self, expr):
        raise NotImplementedError("GlobalSubscriptCounter encountered "
                                  "derivative, "
                                  "map_derivative not implemented.")

    def map_slice(self, expr):
        raise NotImplementedError("GlobalSubscriptCounter encountered slice, "
                                  "map_slice not implemented.")

class RegisterUsageEstimator(CombineMapper):

    def __init__(self, knl):
        self.knl = knl
        from loopy.expression import TypeInferenceMapper
        self.type_inf = TypeInferenceMapper(knl)
        self.vars_found = []
        self.subs_found = []

    def combine(self, values):
        return sum(values)

    def forget_prev_vars(self):
        del self.vars_found[:]

    def forget_prev_subs(self):
        del self.subs_found[:]

    def map_constant(self, expr):
        return 0
    #'''
    def map_variable(self, expr):
        if expr in self.vars_found:
            return 0
        else:
            self.vars_found.append(expr)
            if "_dim_" in str(expr): #TODO how to remove block/thread size/id vars?
                return 0
            else:
                return 1

    map_tagged_variable = map_variable

    #map_variable = map_tagged_variable
    map_call = map_constant  # TODO what is this?

    def map_subscript(self, expr):
        name = expr.aggregate.name  # name of array

        if name in self.knl.arg_dict:
            # not a temporary variable
            array = self.knl.arg_dict[name]
        elif self.knl.temporary_variables[name].is_local:
            # temp var is in shared mem
            return 0 + self.rec(expr.index)
        elif (expr.index, expr.aggregate) in self.subs_found:
            # temp var is NOT shared, but already counted
            return 0 + self.rec(expr.index)
        else:
            # temp var is NOT shared and NOT already counted
            self.subs_found.append((expr.index, expr.aggregate))
            return 1 + self.rec(expr.index)

        # expr is not a temporary variable

        if not isinstance(array, lp.GlobalArg):
            print("debug... When does this happen? ", expr, array)
            1/0
            # this array is not in global memory
            return 1 + self.rec(expr.index)  # TODO

        # this is a global mem access
        if (expr.index, expr.aggregate) in self.subs_found:
            return 0 + self.rec(expr.index)
        else:
            self.subs_found.append((expr.index, expr.aggregate))
            return 1 + self.rec(expr.index)

    def map_sum(self, expr):
        if expr.children:
            return sum(self.rec(child) for child in expr.children)
        else:
            return 0  # TODO when does this happen?

    map_product = map_sum

    def map_quotient(self, expr, *args):
        return self.rec(expr.numerator) + self.rec(expr.denominator)

    map_floor_div = map_quotient
    map_remainder = map_quotient

    def map_power(self, expr):
        return self.rec(expr.base) + self.rec(expr.exponent)

    def map_left_shift(self, expr):
        return self.rec(expr.shiftee)+self.rec(expr.shift)

    map_right_shift = map_left_shift

    def map_bitwise_not(self, expr):
        return self.rec(expr.child)

    def map_bitwise_or(self, expr):
        return sum(self.rec(child) for child in expr.children)

    map_bitwise_xor = map_bitwise_or
    map_bitwise_and = map_bitwise_or

    def map_comparison(self, expr):
        return self.rec(expr.left)+self.rec(expr.right)

    map_logical_not = map_bitwise_not
    map_logical_or = map_bitwise_or
    map_logical_and = map_logical_or

    def map_if(self, expr):
        warnings.warn("RegisterUsageEstimator counting register usage as "
                      "sum of if-statement branches.")
        return self.rec(expr.condition) + self.rec(expr.then) + self.rec(expr.else_)

    def map_if_positive(self, expr):
        warnings.warn("RegisterUsageEstimator counting register usage as "
                      "sum of if_pos-statement branches.")
        return self.rec(expr.criterion) + self.rec(expr.then) + self.rec(expr.else_)

    map_min = map_bitwise_or
    map_max = map_min

    def map_common_subexpression(self, expr):
        raise NotImplementedError("GlobalSubscriptCounter encountered "
                                  "common_subexpression, "
                                  "map_common_subexpression not implemented.")

    def map_substitution(self, expr):
        raise NotImplementedError("GlobalSubscriptCounter encountered "
                                  "substitution, "
                                  "map_substitution not implemented.")

    def map_derivative(self, expr):
        raise NotImplementedError("GlobalSubscriptCounter encountered "
                                  "derivative, "
                                  "map_derivative not implemented.")

    def map_slice(self, expr):
        raise NotImplementedError("GlobalSubscriptCounter encountered slice, "
                                  "map_slice not implemented.")

def count(kernel, bset):
    try:
        return bset.card()
    except AttributeError:
        pass

    if not bset.is_box():
        from loopy.diagnostic import warn
        warn(kernel, "count_overestimate",
                "Barvinok wrappers are not installed. "
                "Counting routines may overestimate the "
                "number of integer points in your loop "
                "domain.")

    result = None

    for i in range(bset.dim(isl.dim_type.set)):
        dmax = bset.dim_max(i)
        dmin = bset.dim_min(i)

        length = isl.PwQPolynomial.from_pw_aff(dmax - dmin + 1)

        if result is None:
            result = length
        else:
            result = result * length

    return result


def get_op_poly(knl):

    """Count the number of operations in a loopy kernel.

    :parameter knl: A :class:`loopy.LoopKernel` whose operations are to be counted.

    :return: A mapping of **{** :class:`numpy.dtype` **:**
             :class:`islpy.PwQPolynomial` **}**.

             - The :class:`islpy.PwQPolynomial` holds the number of operations for
               the :class:`numpy.dtype` specified in the key (in terms of the
               :class:`loopy.LoopKernel` *inames*).

    Example usage::

        # (first create loopy kernel and specify array data types)

        poly = get_op_poly(knl)
        params = {'n': 512, 'm': 256, 'l': 128}
        float32_op_ct = poly.dict[np.dtype(np.float32)].eval_with_dict(params)
        float64_op_ct = poly.dict[np.dtype(np.float64)].eval_with_dict(params)

        # (now use these counts to predict performance)

    """

    from loopy.preprocess import preprocess_kernel, infer_unknown_types
    knl = infer_unknown_types(knl, expect_completion=True)
    knl = preprocess_kernel(knl)

    op_poly = ToCountMap()
    op_counter = ExpressionOpCounter(knl)
    for insn in knl.instructions:
        # how many times is this instruction executed?
        # check domain size:
        insn_inames = knl.insn_inames(insn)
        inames_domain = knl.get_inames_domain(insn_inames)
        domain = (inames_domain.project_out_except(insn_inames, [dim_type.set]))
        ops = op_counter(insn.assignee) + op_counter(insn.expression)
        op_poly = op_poly + ops*count(knl, domain)
    return op_poly.dict


def get_gmem_access_poly(knl):  # for now just counting subscripts
    """Count the number of global memory accesses in a loopy kernel.

    :parameter knl: A :class:`loopy.LoopKernel` whose DRAM accesses are to be
                    counted.

    :return: A mapping of **{(** :class:`numpy.dtype` **,** :class:`string` **,**
             :class:`string` **)** **:** :class:`islpy.PwQPolynomial` **}**.

             - The :class:`numpy.dtype` specifies the type of the data being
               accessed.

             - The first string in the map key specifies the global memory
               access type as
               *consecutive*, *nonconsecutive*, or *uniform*.

             - The second string in the map key specifies the global memory
               access type as a
               *load*, or a *store*.

             - The :class:`islpy.PwQPolynomial` holds the number of DRAM accesses
               with the characteristics specified in the key (in terms of the
               :class:`loopy.LoopKernel` *inames*).

    Example usage::

        # (first create loopy kernel and specify array data types)

        subscript_map = get_gmem_access_poly(knl)
        params = {'n': 512, 'm': 256, 'l': 128}

        f32_uncoalesced_load = subscript_map.dict[
                            (np.dtype(np.float32), 'nonconsecutive', 'load')
                            ].eval_with_dict(params)
        f32_coalesced_load = subscript_map.dict[
                            (np.dtype(np.float32), 'consecutive', 'load')
                            ].eval_with_dict(params)
        f32_coalesced_store = subscript_map.dict[
                            (np.dtype(np.float32), 'consecutive', 'store')
                            ].eval_with_dict(params)

        # (now use these counts to predict performance)

    """

    from loopy.preprocess import preprocess_kernel, infer_unknown_types
    knl = infer_unknown_types(knl, expect_completion=True)
    knl = preprocess_kernel(knl)

    subs_poly = ToCountMap()
    subscript_counter = GlobalSubscriptCounter(knl)
    for insn in knl.instructions:
        insn_inames = knl.insn_inames(insn)
        inames_domain = knl.get_inames_domain(insn_inames)
        domain = (inames_domain.project_out_except(insn_inames, [dim_type.set]))
        subs_expr = subscript_counter(insn.expression)
        subs_expr = ToCountMap(dict(
            (key + ("load",), val)
            for key, val in six.iteritems(subs_expr.dict)))

        subs_assignee = subscript_counter(insn.assignee)
        subs_assignee = ToCountMap(dict(
            (key + ("store",), val)
            for key, val in six.iteritems(subs_assignee.dict)))

        subs_poly = subs_poly + (subs_expr + subs_assignee)*count(knl, domain)
    return subs_poly.dict


def get_DRAM_access_poly(knl):
    from warnings import warn
    warn("get_DRAM_access_poly is deprecated. Use get_gmem_access_poly instead",
            DeprecationWarning, stacklevel=2)
    return get_gmem_access_poly(knl)


def sum_mem_access_to_bytes(m):
    """Sum the mapping returned by :func:`get_gmem_access_poly` to a mapping

    **{(** :class:`string` **,** :class:`string` **)**
    **:** :class:`islpy.PwQPolynomial` **}**

    i.e., aggregate the transfer numbers for all types into a single byte count.
    """

    result = {}
    for (dtype, kind, direction), v in m.items():
        new_key = (kind, direction)
        bytes_transferred = int(dtype.itemsize) * v
        if new_key in result:
            result[new_key] += bytes_transferred
        else:
            result[new_key] = bytes_transferred

    return result


def get_barrier_poly(knl):

    """Count the number of barriers each thread encounters in a loopy kernel.

    :parameter knl: A :class:`loopy.LoopKernel` whose barriers are to be counted.

    :return: An :class:`islpy.PwQPolynomial` holding the number of barrier calls
             made (in terms of the :class:`loopy.LoopKernel` *inames*).

    Example usage::

        # (first create loopy kernel and specify array data types)

        barrier_poly = get_barrier_poly(knl)
        params = {'n': 512, 'm': 256, 'l': 128}
        barrier_count = barrier_poly.eval_with_dict(params)

        # (now use this count to predict performance)

    """

    from loopy.preprocess import preprocess_kernel, infer_unknown_types
    from loopy.schedule import EnterLoop, LeaveLoop, Barrier
    from operator import mul
    knl = infer_unknown_types(knl, expect_completion=True)
    knl = preprocess_kernel(knl)
    knl = lp.get_one_scheduled_kernel(knl)
    iname_list = []
    barrier_poly = isl.PwQPolynomial('{ 0 }')

    for sched_item in knl.schedule:
        if isinstance(sched_item, EnterLoop):
            if sched_item.iname:  # (if not empty)
                iname_list.append(sched_item.iname)
        elif isinstance(sched_item, LeaveLoop):
            if sched_item.iname:  # (if not empty)
                iname_list.pop()
        elif isinstance(sched_item, Barrier):
            if iname_list:  # (if iname_list is not empty)
                ct = (count(knl, (
                                knl.get_inames_domain(iname_list).
                                project_out_except(iname_list, [dim_type.set])
                                )), )
                barrier_poly += reduce(mul, ct)
            else:
                barrier_poly += isl.PwQPolynomial('{ 1 }')

    return barrier_poly


def get_regs_per_thread(knl):
    return get_regs_per_thread3_2(knl)


# map_var and map_tagged_var returned 1, no checking for any duplication
def get_regs_per_thread1(knl):

    """Estimate registers per thread usage by a loopy kernel.

    :parameter knl: A :class:`loopy.LoopKernel` whose reg usage will be estimated.

    """

    from loopy.preprocess import preprocess_kernel, infer_unknown_types
    from loopy.schedule import EnterLoop, LeaveLoop, Barrier, RunInstruction
    from operator import mul
    knl = infer_unknown_types(knl, expect_completion=True)
    knl = preprocess_kernel(knl)
    knl = lp.get_one_scheduled_kernel(knl)

    max_regs = 0
    current_loop_indices = 0
    reg_counter = RegisterUsageEstimator(knl)

    #TODO test blocks vs lines
    for sched_item in knl.schedule:
        if isinstance(sched_item, EnterLoop):
            # need to add indices to index count
            # if counting by blocks, check current blk total vs max, save if bigger
            if sched_item.iname:  # (if not empty)
                current_loop_indices += 1  # TODO assumes all loops add 1 new index
                #print("enter loop: ", sched_item)
        elif isinstance(sched_item, LeaveLoop):
            # need to subtract indices from index count
            # if counting by blocks, check current blk total vs max, save if bigger
            if sched_item.iname:  # (if not empty)
                current_loop_indices -= 1  # TODO assumes all loops add 1 new index
                #print("leave loop: ", sched_item)
        elif isinstance(sched_item, RunInstruction):
            # count regs for this instruction
            # if counting by blocks, add to current block total
            # if counting by lines, check current line total vs max, save if bigger
            insn = knl.id_to_insn[sched_item.insn_id]
            regs = current_loop_indices + \
                   reg_counter(insn.assignee) + \
                   reg_counter(insn.expression)
            if regs > max_regs:
                max_regs = regs
            #print("RunInstruction, regs, max_regs ", sched_item, regs, max_regs)
            # TODO check for iname reuse
            # TODO don't count variables if they are loop indices?

    return max_regs

# no duplicate vars, subs
def get_regs_per_thread2(knl):

    """Estimate registers per thread usage by a loopy kernel.

    :parameter knl: A :class:`loopy.LoopKernel` whose reg usage will be estimated.

    """

    from loopy.preprocess import preprocess_kernel, infer_unknown_types
    from loopy.schedule import EnterLoop, LeaveLoop, Barrier, RunInstruction
    from operator import mul
    knl = infer_unknown_types(knl, expect_completion=True)
    knl = preprocess_kernel(knl)
    knl = lp.get_one_scheduled_kernel(knl)
    #print(knl)
    max_regs = 0
    current_loop_indices = 0
    reg_counter = RegisterUsageEstimator(knl)

    #TODO test blocks vs lines
    for sched_item in knl.schedule:
        reg_counter.forget_prev_vars()
        reg_counter.forget_prev_subs()
        if isinstance(sched_item, EnterLoop):
            # need to add indices to index count
            # if counting by blocks, check current blk total vs max, save if bigger
            if sched_item.iname:  # (if not empty)
                current_loop_indices += 1  # TODO assumes all loops add 1 new index
                #print("enter loop: ", sched_item)
        elif isinstance(sched_item, LeaveLoop):
            # need to subtract indices from index count
            # if counting by blocks, check current blk total vs max, save if bigger
            if sched_item.iname:  # (if not empty)
                current_loop_indices -= 1  # TODO assumes all loops add 1 new index
                #print("leave loop: ", sched_item)
        elif isinstance(sched_item, RunInstruction):
            # count regs for this instruction
            # if counting by blocks, add to current block total
            # if counting by lines, check current line total vs max, save if bigger
            insn = knl.id_to_insn[sched_item.insn_id]
            regs = current_loop_indices + \
                   reg_counter(insn.assignee) + \
                   reg_counter(insn.expression)
            if regs > max_regs:
                max_regs = regs
            #print("RunInstruction, regs, max_regs ", sched_item, regs, max_regs)
            # TODO check for iname reuse
            # TODO don't count variables if they are loop indices?

    return max_regs

def get_regs_per_thread3(knl):

    """Estimate registers per thread usage by a loopy kernel.

    :parameter knl: A :class:`loopy.LoopKernel` whose reg usage will be estimated.

    """

    from loopy.preprocess import preprocess_kernel, infer_unknown_types
    from loopy.schedule import EnterLoop, LeaveLoop, Barrier, RunInstruction
    from operator import mul
    knl = infer_unknown_types(knl, expect_completion=True)
    knl = preprocess_kernel(knl)
    knl = lp.get_one_scheduled_kernel(knl)
    #print(knl)
    max_regs = 0
    current_loop_indices = 0
    block_reg_totals = [0]
    reg_counters = [RegisterUsageEstimator(knl)]
    # multiple counters to track nested sets of previously used iname+index combinations

    for sched_item in knl.schedule:
        if isinstance(sched_item, EnterLoop):
            if sched_item.iname:  # (if not empty)
                #print("entering loop, totals: \n", block_reg_totals, max_regs) 
                current_loop_indices += 1  # TODO assumes all loops add 1 new index
                # start a new block total
                block_reg_totals.append(current_loop_indices)
                # start a new estimator
                reg_counters.append(RegisterUsageEstimator(knl))
                #print("entered loop, totals: \n", block_reg_totals, max_regs) 
            else:
                print("Error, how does this happen?")
                1/0

        elif isinstance(sched_item, LeaveLoop):
            if sched_item.iname:  # (if not empty)
                #print("leaving loop, totals: \n", block_reg_totals, max_regs) 
                current_loop_indices -= 1  # TODO assumes all loops add 1 new index
                if block_reg_totals[-1] > max_regs:
                    max_regs = block_reg_totals[-1]
                # pop to resume previous total
                #block_reg_totals[-2] += block_reg_totals[-1]
                block_reg_totals.pop()
                reg_counters.pop()
                #print("left loop, totals: \n", block_reg_totals, max_regs) 
            else:
                print("Error, how does this happen?")
                1/0
        elif isinstance(sched_item, RunInstruction):
            insn = knl.id_to_insn[sched_item.insn_id]
            #print("instruction found: ", insn) 
            #print("pre insn totals: \n", block_reg_totals, max_regs) 
            block_reg_totals[-1] += reg_counters[-1](insn.assignee) + \
                                    reg_counters[-1](insn.expression)
            #print("post insn totals: \n", block_reg_totals, max_regs) 
            # TODO don't count variables if they are loop indices? (also try this with ctr2)

    #print("finished schedule, totals: \n", block_reg_totals, max_regs)
    # finished looping, check outer block
    if block_reg_totals[-1] > max_regs:
        max_regs = block_reg_totals[-1]
    #print("final, totals: \n", block_reg_totals, max_regs)

    return max_regs

def get_regs_per_thread3_2(knl):

    """Estimate registers per thread usage by a loopy kernel.

    :parameter knl: A :class:`loopy.LoopKernel` whose reg usage will be estimated.

    """

    from loopy.preprocess import preprocess_kernel, infer_unknown_types
    from loopy.schedule import EnterLoop, LeaveLoop, Barrier, RunInstruction
    from operator import mul
    knl = infer_unknown_types(knl, expect_completion=True)
    knl = preprocess_kernel(knl)
    knl = lp.get_one_scheduled_kernel(knl)
    #print(knl)
    max_regs = 0
    #current_loop_indices = 0
    block_reg_totals = [0]
    reg_counters = [RegisterUsageEstimator(knl)]
    # multiple counters to track nested sets of previously used iname+index combinations

    for sched_item in knl.schedule:
        if isinstance(sched_item, EnterLoop):
            if sched_item.iname:  # (if not empty)
                #print("entering loop, totals: \n", block_reg_totals, max_regs) 
                #current_loop_indices += 1  # TODO assumes all loops add 1 new index
                # start a new block total
                #block_reg_totals.append(current_loop_indices)
                block_reg_totals.append(0)
                # start a new estimator
                reg_counters.append(RegisterUsageEstimator(knl))
                #print("entered loop, totals: \n", block_reg_totals, max_regs) 
            else:
                print("Error, how does this happen?")
                1/0

        elif isinstance(sched_item, LeaveLoop):
            if sched_item.iname:  # (if not empty)
                #print("leaving loop, totals: \n", block_reg_totals, max_regs) 
                #current_loop_indices -= 1  # TODO assumes all loops add 1 new index
                if block_reg_totals[-1] > max_regs:
                    max_regs = block_reg_totals[-1]
                # pop to resume previous total
                #block_reg_totals[-2] += block_reg_totals[-1]
                block_reg_totals.pop()
                reg_counters.pop()
                #print("left loop, totals: \n", block_reg_totals, max_regs) 
            else:
                print("Error, how does this happen?")
                1/0
        elif isinstance(sched_item, RunInstruction):
            insn = knl.id_to_insn[sched_item.insn_id]
            #print("instruction found: ", insn) 
            #print("pre insn totals: \n", block_reg_totals, max_regs) 
            block_reg_totals[-1] += reg_counters[-1](insn.assignee) + \
                                    reg_counters[-1](insn.expression)
            #print("post insn totals: \n", block_reg_totals, max_regs) 
            # TODO don't count variables if they are loop indices? (also try this with ctr2)

    #print("finished schedule, totals: \n", block_reg_totals, max_regs)
    # finished looping, check outer block
    if block_reg_totals[-1] > max_regs:
        max_regs = block_reg_totals[-1]
    #print("final, totals: \n", block_reg_totals, max_regs)

    return max_regs

#add all sub blocks to containing block
#aka add everything together
def get_regs_per_thread4(knl):

    """Estimate registers per thread usage by a loopy kernel.

    :parameter knl: A :class:`loopy.LoopKernel` whose reg usage will be estimated.

    """

    from loopy.preprocess import preprocess_kernel, infer_unknown_types
    from loopy.schedule import EnterLoop, LeaveLoop, Barrier, RunInstruction
    from operator import mul
    knl = infer_unknown_types(knl, expect_completion=True)
    knl = preprocess_kernel(knl)
    knl = lp.get_one_scheduled_kernel(knl)
    #print(knl)

    regs = 0
    max_loop_indices = 0
    current_loop_indices = 0
    reg_counter = RegisterUsageEstimator(knl)

    for sched_item in knl.schedule:
        if isinstance(sched_item, EnterLoop):
            if sched_item.iname:  # (if not empty)
                current_loop_indices += 1  # TODO assumes all loops add 1 new index
                if current_loop_indices > max_loop_indices:
                    max_loop_indices = current_loop_indices
                #print("enter loop: ", sched_item)
        elif isinstance(sched_item, LeaveLoop):
            # need to subtract indices from index count
            if sched_item.iname:  # (if not empty)
                current_loop_indices -= 1  # TODO assumes all loops add 1 new index
                #print("leave loop: ", sched_item)
        elif isinstance(sched_item, RunInstruction):
            # count regs for this instruction
            insn = knl.id_to_insn[sched_item.insn_id]
            regs += reg_counter(insn.assignee) + \
                   reg_counter(insn.expression)

    return regs+max_loop_indices
