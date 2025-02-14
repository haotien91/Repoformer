#!/usr/bin/env python
# coding=utf-8

import argparse
import json
from datasets import load_dataset
from transformers import AutoTokenizer
from vllm import LLM, SamplingParams
from eval_metric import compute_metric_stmt
from eval_metric_cceval import compute_metric_stmt_cceval
from tqdm import tqdm
import time


def prepare_prompt(tokenizer, task, model_type, left_cxt, right_cxt=None, crossfile_cxt=None):
    if model_type == "codelm_leftright_context":
        left_cxt_truncated = tokenizer.decode(tokenizer.encode(left_cxt)[-(args.max_seq_length - args.gen_length - args.right_context_length):])
        right_cxt_truncated = tokenizer.decode(tokenizer.encode(right_cxt)[:args.right_context_length])
        prompt = f'<fim_prefix>{left_cxt_truncated}<fim_suffix>{right_cxt_truncated}<fim_middle>'
    elif model_type == "codelm_right_cfc_left":
        assert crossfile_cxt is not None
        left_cxt_truncated = tokenizer.decode(tokenizer.encode(left_cxt)[-(args.max_seq_length - args.gen_length - args.right_context_length - args.cfc_seq_length):])
        right_cxt_truncated = tokenizer.decode(tokenizer.encode(right_cxt)[:args.right_context_length])
        crossfile_cxt_truncated = tokenizer.decode(tokenizer.encode('\n\n' + crossfile_cxt)[:args.cfc_seq_length])
        prompt = f'<fim_prefix>{left_cxt_truncated}<fim_suffix>{right_cxt_truncated}{crossfile_cxt_truncated}<fim_middle>'
    else:
        raise NotImplementedError
    return prompt


def build_dataset(args):
    tokenizer = AutoTokenizer.from_pretrained(args.model_name_or_path)

    with open(args.prompt_file) as f:
        raw_data = [json.loads(line) for line in f.readlines()]

    data = []
    for entry in raw_data:
        task = args.task
        model_type = args.model_type
        left_cxt = entry["prompt"]
        right_cxt = entry["right_context"]
        crossfile_cxt = None
        if 'crossfile_context' in entry:
            crossfile_cxt = entry["crossfile_context"] if type(entry["crossfile_context"]) == str else entry["crossfile_context"]['text']            
        prompt = prepare_prompt(tokenizer, task, model_type, left_cxt, right_cxt, crossfile_cxt)
        entry['llm_prompt'] = prompt
        data.append(entry)
    
    return data
    

def model_inference(args):
    llm = LLM(model=args.model_name_or_path)
    sampling_params = SamplingParams(temperature=0, top_p=1, max_tokens=args.gen_length)

    data = build_dataset(args)

    all_preds = []
    for entry in tqdm(data):
        start_time = time.time_ns()
        cur_pred = llm.generate(entry['llm_prompt'], sampling_params, use_tqdm=False)
        end_time = time.time_ns()
        latency = (end_time - start_time) / 1_000_000_000
                
        all_preds.append({
            "task_id": entry["metadata"]["task_id"],
            "pred": cur_pred[0].outputs[0].text,
            "latency": latency
            # "prompt": entry["llm_prompt"]
        })
        
    with open(f"{args.output_dir}/prediction.jsonl", "w", encoding="utf-8") as f_pred:
        for entry in all_preds:
            f_pred.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    
    parser.add_argument("--language", type=str, required=True, help="language name")
    parser.add_argument("--model_name_or_path", type=str, required=True)
    parser.add_argument(
        "--model_type",
        type=str,
        default="codelm",
        choices=["codelm", "codelm_cfc", "codelm_leftright_context", 'codelm_right_cfc_left'],
        help="Model type to be loaded"
    )
    parser.add_argument("--prompt_file", type=str, default=None, help="file with a list of prompts")
    parser.add_argument("--gen_length", type=int, default=50, help="max length of generated token sequence")
    parser.add_argument("--max_seq_length", type=int, default=2048, help="max length of prompt")
    parser.add_argument(
        "--cfc_seq_length",
        type=int,
        default=512,
        help="For model_type=codelm_cfc: Text sequence length corresponding to the retrieved nodes"
    )
    parser.add_argument(
        "--right_context_length",
        type=int,
        default=512,
        help="For model_type=codelm_leftright_context: Text sequence length corresponding to the right context"
    )
    parser.add_argument("--output_dir", type=str, default="output_dir", help="output directory to save predictions")
    parser.add_argument("--num_return_sequences", type=int, default=1, help="The number of samples to generate.")
    # compute metric args
    parser.add_argument(
        "--ts_lib",
        type=str,
        default="build/python-lang-parser.so",
        help="tree-sitter lib for tokenize code"
    )
    # only compute metric
    parser.add_argument("--only_compute_metric", action="store_true", help="only compute metric")
    # for cceval metric
    parser.add_argument("--compute_cceval_metric", action='store_true', help="use cceval metric")
    parser.add_argument(
        "--task",
        choices=["line_completion", "api_completion", "function_completion"],
        default="line_completion",
        help="task name"
    )
    args = parser.parse_args()

    model_inference(args)

    if args.compute_cceval_metric:
        compute_metric_stmt_cceval(args)
    else:
        compute_metric_stmt(args)
