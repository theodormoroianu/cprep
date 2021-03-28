import subprocess
from lib.base import EvalResult, File, ProblemCfg, TestCase
from typing import Optional, List
import time 
from lib import compiler, evaluation, generation
from lib.utils import pad 
from colorama import Style, Fore 
import os 
import contextlib
import functools
from lib.files import Files 


RED_CROSS = f'{Fore.RED}\u00d7{Fore.RESET}'
GREEN_TICK = f'{Fore.GREEN}\u2713{Fore.RESET}'


NON_DETERMINISTIC_WARNING = """\
[W] Generator '{name}' seems to be non-deterministic. 
While this is supported, it may cause problems with reproductibility.
Please make your generator deterministic, by setting the random seed either as constant, or as command argument.
   Example: `int seed = stoi(argv[1]); srand(seed);`
"""


def discover_files(cfg, solutions=None, base_dir=""):
    # If solutions are specified, discard the discovered solutions
    # and use the provided ones instead.
    files = Files(base_dir, cfg)
    if solutions:
        files.files = [f for f in files.files if f.kind != 'solution']
        for src_path in solutions:
            files.files.append(File(src_path=src_path, kind='solution'))
        
    print("Discovered files: ")
    pad_len = max(len(f.src_path) for f in files.files)
    for f in files.files:
        print(f" - {pad(f.src_path, pad_len)} {Style.BRIGHT}({f.kind}){Style.RESET_ALL}")
    print()

    return files


def compile_files(files: Files, temp_dir: str, cfg):
    print("Compiling all cpp files...")
    solutions = [f for f in files.files if f.ext == 'cpp']
    pad_len = max(len(f.name) for f in solutions)
    for f in solutions:
        print(f" - {pad(f.name, pad_len)} ", end='', flush=True)
        output_dir = os.path.join(temp_dir, cfg['output_dir'])
        compiled, used_cache = compiler.compile(f, 
            gcc_path=cfg['g++'], gcc_args=cfg['args'], 
            output_dir=output_dir)

        line = GREEN_TICK if compiled else RED_CROSS
        if used_cache:
            line += f" {Style.DIM}(cached){Style.RESET_ALL}"
        print(line)
    print()


def compute_evaluation_results(
        files: Files, 
        test_cases: List[TestCase], 
        cfg: ProblemCfg):
    solution_files = files.solutions 
    checker_file = files.checker

    col_len = 15
    header_str = ' '.join([' ' + pad('#', 3)] + [pad(f.name, col_len) for f in solution_files])
    table_len = len(header_str)

    print("Evaluation results:")
    print(header_str)
    print('=' * table_len)

    last_group_idx = 0
    for tc in test_cases:
        if last_group_idx != tc.group_idx:
            print('-' * table_len)
        last_group_idx = tc.group_idx

        print(' ' + pad(str(tc.idx), 3), end=' ', flush=True)

        # Evaluate and print results for each solution.
        for sol in solution_files:
            if not tc.generated:
                print(pad(f"{Style.DIM}-{Style.RESET_ALL}", col_len), end=' ', flush=True)
                continue
            res = evaluation.evaluate_solution(
                sol, tc.input_text, tc.answer_text, 
                cfg=cfg, 
                checker_file=checker_file)
            verdict = res.verdict
            time_limit_ms = cfg.time_limit_ms
            while len(verdict) < 3:
                verdict += ' '
            if res.verdict in ['TLE', 'AC'] and time_limit_ms * 0.8 < res.time_exec_ms < time_limit_ms * 1.2:
                verdict = Fore.YELLOW + verdict + Fore.RESET
            elif res.verdict == 'AC':
                verdict = Fore.GREEN + verdict + Fore.RESET
            else:
                verdict = Fore.RED + verdict + Fore.RESET
            cell_text = f"{verdict}"
            if res.time_exec_ms >= 0:
                cell_text += f" {Style.DIM}({round(res.time_exec_ms)} ms){Style.RESET_ALL}"
            print(pad(cell_text, col_len), end=' ', flush=True)
        print()
    print('=' * table_len)
    print()


def _generate_test_cases(
        test_cases: List[TestCase], 
        files: Files, 
        tests_dir: str, 
        num_workers: int,
        cfg: ProblemCfg):
        
    print("Generating test cases...")
    print(f"Model solution: {Style.BRIGHT}{cfg.model_solution}{Style.RESET_ALL}")

    if not files.validators:
        print()
        print(Fore.YELLOW + "[W] No validators found. It is recommended to have validators, " +
            "to check generator output." + Fore.RESET)
        print()
    checker_file = files.checker

    idx = 1
    print(" ", end="")
    last_group_idx = 0
    for tc in test_cases:
        if tc.group_idx != last_group_idx:
            print("| ", end="")
        last_group_idx = tc.group_idx

        # Actual generation happens here.
        valid = generation.generate_test_case(
            tc, files, cfg, num_workers=num_workers)
        output = GREEN_TICK if valid else RED_CROSS
        if valid:
            if tc.info:
                output = f"[{output} {tc.info}]"
            # Write tests to disk.
            os.makedirs(tests_dir, exist_ok=True)
            with open(os.path.join(tests_dir, 
                    cfg.input_pattern.format(idx=tc.idx)), 'wb') as f:
                f.write(tc.input_text)
            with open(os.path.join(tests_dir, 
                    cfg.answer_pattern.format(idx=tc.idx)), 'wb') as f:
                f.write(tc.answer_text)
        else:
            tc.input_text = tc.answer_text = None

        print(output, end=" ", flush=True)
    print()
    print()
    return test_cases


def generate_test_cases(
        test_cases: List[TestCase], 
        files: Files, 
        cfg: dict):

    tests_dir = cfg['tests_dir']
    generate = functools.partial(
        _generate_test_cases, test_cases, files, 
        tests_dir, cfg['num_workers'], cfg['problem'])

    tick = time.time()
    generate()
    tock = time.time()

    if cfg['run_deterministic_check']:
        time.sleep(max(0.1, 1.1 - (tock - tick)))
        chk_test_cases = test_cases.copy()
        with contextlib.redirect_stdout(open(os.devnull, 'w')):
            generate()
        nd_generators = set()
        for tc1, tc2 in zip(test_cases, chk_test_cases):
            if tc1.generator_name in nd_generators or tc1.input_text == tc2.input_text:
                continue
            print(Fore.YELLOW + Style.BRIGHT + NON_DETERMINISTIC_WARNING.format(
                name=tc1.generator_name) + Fore.RESET + Style.RESET_ALL)
            input("Press [ENTER] to continue: ")
            print()
            nd_generators.add(tc1.generator_name)
    return test_cases