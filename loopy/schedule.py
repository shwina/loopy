from __future__ import division

from pytools import Record
import sys




# {{{ schedule items

class EnterLoop(Record):
    __slots__ = ["iname"]

class LeaveLoop(Record):
    __slots__ = ["iname"]

class RunInstruction(Record):
    __slots__ = ["insn_id"]

class Barrier(Record):
    __slots__ = ["comment"]

# }}}

# {{{ schedule utilities

def gather_schedule_subloop(schedule, start_idx):
    assert isinstance(schedule[start_idx], EnterLoop)
    level = 0

    i = start_idx
    while i < len(schedule):
        if isinstance(schedule[i], EnterLoop):
            level += 1
        if isinstance(schedule[i], LeaveLoop):
            level -= 1

            if level == 0:
                return schedule[start_idx:i+1], i+1

        i += 1

    assert False




def get_barrier_needing_dependency(kernel, target, source, unordered=False):
    from loopy.kernel import Instruction
    if not isinstance(source, Instruction):
        source = kernel.id_to_insn[source]
    if not isinstance(target, Instruction):
        target = kernel.id_to_insn[target]

    local_vars = kernel.local_var_names()

    tgt_write = set([target.get_assignee_var_name()]) & local_vars
    tgt_read = target.get_read_var_names() & local_vars

    src_write = set([source.get_assignee_var_name()]) & local_vars
    src_read = source.get_read_var_names() & local_vars

    waw = tgt_write & src_write
    raw = tgt_read & src_write
    war = tgt_write & src_read

    for var_name in raw | war:
        if not unordered:
            assert source.id in target.insn_deps
        return (target, source, var_name)

    if source is target:
        return None

    for var_name in waw:
        assert (source.id in target.insn_deps
                or source is target)
        return (target, source, var_name)

    return None






def get_barrier_dependent_in_schedule(kernel, source, schedule,
        unordered):
    """
    :arg source: an instruction id for the source of the dependency
    """

    for sched_item in schedule:
        if isinstance(sched_item, RunInstruction):
            temp_res = get_barrier_needing_dependency(
                    kernel, sched_item.insn_id, source, unordered=unordered)
            if temp_res:
                return temp_res
        elif isinstance(sched_item, Barrier):
            return




def find_active_inames_at(kernel, sched_index):
    active_inames = []

    from loopy.schedule import EnterLoop, LeaveLoop
    for sched_item in kernel.schedule[:sched_index]:
        if isinstance(sched_item, EnterLoop):
            active_inames.append(sched_item.iname)
        if isinstance(sched_item, LeaveLoop):
            active_inames.pop()

    return set(active_inames)




def has_barrier_within(kernel, sched_index):
    sched_item = kernel.schedule[sched_index]

    if isinstance(sched_item, EnterLoop):
        loop_contents, _ = gather_schedule_subloop(
                kernel.schedule, sched_index)
        from pytools import any
        return any(isinstance(subsched_item, Barrier)
                for subsched_item in loop_contents)
    elif isinstance(sched_item, Barrier):
        return True
    else:
        return False




def find_used_inames_within(kernel, sched_index):
    sched_item = kernel.schedule[sched_index]

    if isinstance(sched_item, EnterLoop):
        loop_contents, _ = gather_schedule_subloop(
                kernel.schedule, sched_index)
        run_insns = [subsched_item
                for subsched_item in loop_contents
                if isinstance(subsched_item, RunInstruction)]
    elif isinstance(sched_item, RunInstruction):
        run_insns = [sched_item]
    else:
        return set()

    result = set()
    for sched_item in run_insns:
        result.update(kernel.insn_inames(sched_item.insn_id))

    return result

# }}}

# {{{ debug help

def dump_schedule(schedule):
    entries = []
    for sched_item in schedule:
        if isinstance(sched_item, EnterLoop):
            entries.append("<%s>" % sched_item.iname)
        elif isinstance(sched_item, LeaveLoop):
            entries.append("</%s>" % sched_item.iname)
        elif isinstance(sched_item, RunInstruction):
            entries.append(sched_item.insn_id)
        elif isinstance(sched_item, Barrier):
            entries.append("|")
        else:
            assert False

    return " ".join(entries)

class ScheduleDebugger:
    def __init__(self, debug_length=None, interactive=True):
        self.longest_rejected_schedule = []
        self.success_counter = 0
        self.dead_end_counter = 0
        self.debug_length = debug_length
        self.interactive = interactive

        self.elapsed_store = 0
        self.start()
        self.wrote_status = 0

        self.update()

    def update(self):
        if ((self.success_counter + self.dead_end_counter) % 50 == 0
                and self.success_counter > 2
                # ^ someone's waiting for the scheduler to go through *all* options
                and (self.debug_length or self.elapsed_time() > 1)
                ):
            sys.stdout.write("\rscheduling... %d successes, "
                    "%d dead ends (longest %d)" % (
                        self.success_counter,
                        self.dead_end_counter,
                        len(self.longest_rejected_schedule)))
            sys.stdout.flush()
            self.wrote_status = 2

    def log_success(self, schedule):
        self.success_counter += 1
        self.update()

    def log_dead_end(self, schedule):
        if len(schedule) > len(self.longest_rejected_schedule):
            self.longest_rejected_schedule = schedule
        self.dead_end_counter += 1
        self.update()

    def done_scheduling(self):
        if self.wrote_status:
            sys.stdout.write("\rscheduler finished"+40*" "+"\n")
            sys.stdout.flush()

    def elapsed_time(self):
        from time import time
        return self.elapsed_store + time() - self.start_time

    def stop(self):
        if self.wrote_status == 2:
            sys.stdout.write("\r"+80*" "+"\n")
            self.wrote_status = 1

        from time import time
        self.elapsed_store += time()-self.start_time

    def start(self):
        from time import time
        self.start_time = time()
# }}}

# {{{ scheduling algorithm

def generate_loop_schedules_internal(kernel, loop_priority, schedule=[], allow_boost=False, debug=None):
    all_insn_ids = set(insn.id for insn in kernel.instructions)

    scheduled_insn_ids = set(sched_item.insn_id for sched_item in schedule
            if isinstance(sched_item, RunInstruction))

    if allow_boost is None:
        rec_allow_boost = None
    else:
        rec_allow_boost = False

    # {{{ find active and entered loops

    active_inames = []
    entered_inames = set()

    for sched_item in schedule:
        if isinstance(sched_item, EnterLoop):
            active_inames.append(sched_item.iname)
            entered_inames.add(sched_item.iname)
        if isinstance(sched_item, LeaveLoop):
            active_inames.pop()

    if active_inames:
        last_entered_loop = active_inames[-1]
    else:
        last_entered_loop = None
    active_inames_set = set(active_inames)

    from loopy.kernel import ParallelTag
    parallel_inames = set(
            iname for iname in kernel.all_inames()
            if isinstance(kernel.iname_to_tag.get(iname), ParallelTag))

    # }}}

    # {{{ decide about debug mode

    debug_mode = False

    if debug is not None:
        if (debug.debug_length is not None
                and len(schedule) >= debug.debug_length):
            debug_mode = True

    if debug_mode:
        print 75*"="
        print "KERNEL:"
        print kernel
        print 75*"="
        print "CURRENT SCHEDULE:"
        print dump_schedule(schedule), len(schedule)
        print "(entry into loop: <iname>, exit from loop: </iname>, instruction names without delimiters)"
        #print "boost allowed:", allow_boost
        print 75*"="
        print "WHY IS THIS A DEAD-END SCHEDULE?"

    #if len(schedule) == 2:
        #from pudb import set_trace; set_trace()

    # }}}

    # {{{ see if any insn can be scheduled now

    # Also take note of insns that have a chance of being schedulable inside
    # the current loop nest, in this set:

    reachable_insn_ids = set()

    unscheduled_insn_ids = all_insn_ids - scheduled_insn_ids

    for insn_id in unscheduled_insn_ids:
        insn = kernel.id_to_insn[insn_id]

        schedule_now = set(insn.insn_deps) <= scheduled_insn_ids

        if not schedule_now:
            if debug_mode:
                print "instruction '%s' is missing insn depedencies '%s'" % (
                        insn.id, ",".join(set(insn.insn_deps) - scheduled_insn_ids))
            continue

        want = kernel.insn_inames(insn) - parallel_inames
        have = active_inames_set - parallel_inames

        # If insn is boostable, it may be placed inside a more deeply
        # nested loop without harm.

        if allow_boost:
            # Note that the inames in 'insn.boostable_into' necessarily won't
            # be contained in 'want'.
            have = have - insn.boostable_into

        if want != have:
            schedule_now = False

            if debug_mode:
                if want-have:
                    print ("instruction '%s' is missing inames '%s'"
                            % (insn.id, ",".join(want-have)))
                if have-want:
                    print ("instruction '%s' won't work under inames '%s'"
                            % (insn.id, ",".join(have-want)))

        # {{{ determine reachability

        if (not schedule_now and have <= want):
            reachable_insn_ids.add(insn_id)

        # }}}

        if schedule_now:
            if debug_mode:
                print "scheduling '%s'" % insn.id
            scheduled_insn_ids.add(insn.id)
            schedule = schedule + [RunInstruction(insn_id=insn.id)]

            # Don't be eager about entering/leaving loops--if progress has been
            # made, revert to top of scheduler and see if more progress can be
            # made.

            for sub_sched in generate_loop_schedules_internal(
                    kernel, loop_priority, schedule,
                    allow_boost=rec_allow_boost, debug=debug):
                yield sub_sched

            return

    unscheduled_insn_ids = list(all_insn_ids - scheduled_insn_ids)

    # }}}

    # {{{ see if we're ready to leave the innermost loop

    if  last_entered_loop is not None:
        can_leave = True

        if last_entered_loop not in kernel.breakable_inames:
            # If the iname is not breakable, then check that we've
            # scheduled all the instructions that require it.

            for insn_id in unscheduled_insn_ids:
                insn = kernel.id_to_insn[insn_id]
                if last_entered_loop in kernel.insn_inames(insn):
                    if debug_mode:
                        print("cannot leave '%s' because '%s' still depends on it"
                                % (last_entered_loop, insn.id))
                    can_leave = False
                    break

        if can_leave:
            can_leave = False

            # We may only leave this loop if we've scheduled an instruction
            # since entering it.

            seen_an_insn = False
            ignore_count = 0
            for sched_item in schedule[::-1]:
                if isinstance(sched_item, RunInstruction):
                    seen_an_insn = True
                elif isinstance(sched_item, LeaveLoop):
                    ignore_count +=1
                elif isinstance(sched_item, EnterLoop):
                    if ignore_count:
                        ignore_count -= 1
                    else:
                        assert sched_item.iname == last_entered_loop
                        if seen_an_insn:
                            can_leave = True
                        break

            if can_leave:
                schedule = schedule + [LeaveLoop(iname=last_entered_loop)]

                for sub_sched in generate_loop_schedules_internal(
                        kernel, loop_priority, schedule,
                        allow_boost=rec_allow_boost, debug=debug):
                    yield sub_sched

                return

    # }}}

    # {{{ see if any loop can be entered now

    # Find inames that are being referenced by as yet unscheduled instructions.
    needed_inames = set()
    for insn_id in unscheduled_insn_ids:
        needed_inames.update(kernel.insn_inames(insn_id))

    needed_inames = (needed_inames
            # There's no notion of 'entering' a parallel loop
            - parallel_inames

            # Don't reenter a loop we're already in.
            - active_inames_set)

    if debug_mode:
        print 75*"-"
        print "inames still needed :", ",".join(needed_inames)
        print "active inames :", ",".join(active_inames)
        print "inames entered so far :", ",".join(entered_inames)
        print "reachable insns:", ",".join(reachable_insn_ids)
        print 75*"-"

    if needed_inames:
        useful_loops = []

        for iname in needed_inames:
            if not kernel.loop_nest_map()[iname] <= active_inames_set | parallel_inames:
                continue

            # {{{ determine if that gets us closer to being able to schedule an insn

            useful = False

            hypothetically_active_loops = active_inames_set | set([iname])
            for insn_id in reachable_insn_ids:
                insn = kernel.id_to_insn[insn_id]

                want = kernel.insn_inames(insn) | insn.boostable_into

                if hypothetically_active_loops <= want:
                    useful = True
                    break

            if not useful:
                if debug_mode:
                    print "iname '%s' deemed not useful" % iname
                continue

            useful_loops.append(iname)

            # }}}

        # {{{ tier building

        # Build priority tiers. If a schedule is found in the first tier, then
        # loops in the second are not even tried (and so on).

        loop_priority_set = set(loop_priority)
        lowest_priority_set = set(kernel.lowest_priority_inames)
        useful_loops_set = set(useful_loops)
        useful_and_desired = useful_loops_set & loop_priority_set

        if useful_and_desired:
            priority_tiers = [[iname]
                    for iname in loop_priority
                    if iname in useful_and_desired
                    and iname not in kernel.lowest_priority_inames]

            priority_tiers.append(
                    set(useful_loops)
                    - loop_priority_set
                    - lowest_priority_set)
        else:
            priority_tiers = [set(useful_loops) - lowest_priority_set]

        priority_tiers.extend([
            [iname]
            for iname in kernel.lowest_priority_inames
            if iname in useful_loops
            ])

        # }}}

        if debug_mode:
            print "useful inames: %s" % ",".join(useful_loops)

        for tier in priority_tiers:
            found_viable_schedule = False

            for iname in tier:
                new_schedule = schedule + [EnterLoop(iname=iname)]

                for sub_sched in generate_loop_schedules_internal(
                        kernel, loop_priority, new_schedule,
                        allow_boost=rec_allow_boost,
                        debug=debug):
                    found_viable_schedule = True
                    yield sub_sched

            if found_viable_schedule:
                return

    # }}}

    if debug_mode:
        print 75*"="
        raw_input("Hit Enter for next schedule:")

    if not active_inames and not unscheduled_insn_ids:
        # if done, yield result
        debug.log_success(schedule)

        yield schedule

    else:
        if not allow_boost and allow_boost is not None:
            # try again with boosting allowed
            for sub_sched in generate_loop_schedules_internal(
                    kernel, loop_priority, schedule=schedule,
                    allow_boost=True, debug=debug):
                yield sub_sched
        else:
            # dead end
            if debug is not None:
                debug.log_dead_end(schedule)

# }}}

# {{{ barrier insertion

def insert_barriers(kernel, schedule, level=0):
    result = []
    owed_barriers = set()

    loop_had_barrier = [False]

    # A 'pre-barrier' is a special case that is only necessary once per loop
    # iteration to protect the tops of local-mem variable assignments from
    # being entered before all reads in the previous loop iteration are
    # complete.  Once the loop has had a barrier, this is not a concern any
    # more, and any further write-after-read hazards will be covered by
    # dependencies for which the 'normal' mechanism below will generate
    # barriers.

    def issue_barrier(is_pre_barrier, dep):
        if result and isinstance(result[-1], Barrier):
            return

        if is_pre_barrier:
            if loop_had_barrier[0] or level == 0:
                return

        owed_barriers.clear()

        cmt = None
        if dep is not None:
            target, source, var = dep
            if is_pre_barrier:
                cmt = "pre-barrier: %s" % var
            else:
                cmt = "dependency: %s" % var

        loop_had_barrier[0] = True
        result.append(Barrier(comment=cmt))

    i = 0
    while i < len(schedule):
        sched_item = schedule[i]

        if isinstance(sched_item, EnterLoop):
            subloop, new_i = gather_schedule_subloop(schedule, i)

            subresult, sub_owed_barriers = insert_barriers(
                    kernel, subloop[1:-1], level+1)

            # {{{ issue dependency-based barriers for contents of nested loop

            # (i.e. if anything *in* the loop depends on something beforehand)

            for insn_id in owed_barriers:
                dep = get_barrier_dependent_in_schedule(kernel, insn_id, subresult,
                        unordered=False)
                if dep:
                    issue_barrier(is_pre_barrier=False, dep=dep)
                    break

            # }}}
            # {{{ issue pre-barriers for contents of nested loop

            if not loop_had_barrier[0]:
                for insn_id in sub_owed_barriers:
                    dep = get_barrier_dependent_in_schedule(
                            kernel, insn_id, schedule, unordered=True)
                    if dep:
                        issue_barrier(is_pre_barrier=True, dep=dep)

            # }}}

            result.append(subloop[0])
            result.extend(subresult)
            result.append(subloop[-1])

            owed_barriers.update(sub_owed_barriers)

            i = new_i

        elif isinstance(sched_item, RunInstruction):
            i += 1

            insn = kernel.id_to_insn[sched_item.insn_id]

            # {{{ issue dependency-based barriers for this instruction

            for dep_src_insn_id in set(insn.insn_deps) & owed_barriers:
                dep = get_barrier_needing_dependency(kernel, insn, dep_src_insn_id)
                if dep:
                    issue_barrier(is_pre_barrier=False, dep=dep)

            # }}}

            assignee_temp_var = kernel.temporary_variables.get(
                    insn.get_assignee_var_name())
            if assignee_temp_var is not None and assignee_temp_var.is_local:
                dep = get_barrier_dependent_in_schedule(kernel, insn.id, schedule,
                        unordered=True)

                if dep:
                    issue_barrier(is_pre_barrier=True, dep=dep)

                result.append(sched_item)
                owed_barriers.add(insn.id)
            else:
                result.append(sched_item)

        else:
            assert False

    return result, owed_barriers

# }}}

# {{{ main scheduling entrypoint

def generate_loop_schedules(kernel, loop_priority=[], debug_args={}):
    from loopy.preprocess import preprocess_kernel
    kernel = preprocess_kernel(kernel)

    from loopy.check import run_automatic_checks
    run_automatic_checks(kernel)

    schedule_count = 0

    debug = ScheduleDebugger(**debug_args)

    generators = [
            generate_loop_schedules_internal(kernel, loop_priority,
                debug=debug, allow_boost=None),
            generate_loop_schedules_internal(kernel, loop_priority,
                debug=debug)]
    for gen in generators:
        for gen_sched in gen:
            gen_sched, owed_barriers = insert_barriers(kernel, gen_sched)
            if owed_barriers:
                from warnings import warn
                from loopy import LoopyAdvisory
                warn("Barrier insertion finished without inserting barriers for "
                        "local memory writes in these instructions: '%s'. "
                        "This often means that local memory was "
                        "written, but never read." % ",".join(owed_barriers), LoopyAdvisory)

            debug.stop()
            yield kernel.copy(schedule=gen_sched)
            debug.start()

            schedule_count += 1

        # if no-boost mode yielded a viable schedule, stop now
        if schedule_count:
            break

    debug.done_scheduling()

    if not schedule_count:
        if debug.interactive:
            print 75*"-"
            print "ERROR: Sorry--loo.py did not find a schedule for your kernel."
            print 75*"-"
            print "Loo.py will now show you the scheduler state at the point"
            print "where the longest (dead-end) schedule was generated, in the"
            print "the hope that some of this makes sense and helps you find"
            print "the issue."
            print
            print "To disable this interactive behavior, pass"
            print "  debug_args=dict(interactive=False)"
            print "to generate_loop_schedules()."
            print 75*"-"
            raw_input("Enter:")
            print
            print

            debug.debug_length = len(debug.longest_rejected_schedule)
            for _ in generate_loop_schedules_internal(kernel, loop_priority,
                    debug=debug):
                pass

        raise RuntimeError("no valid schedules found")

# }}}





# vim: foldmethod=marker
