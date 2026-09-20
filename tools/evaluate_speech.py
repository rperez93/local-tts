"""Optional local ASR evaluation of a calibration report (requires faster-whisper).

Run from its own venv. Nothing is uploaded; no synthesis/playback/settings changes.
ASR error rates are intelligibility proxies, never naturalness or phoneme scores.
"""
import argparse
import json
import re
import unicodedata
from pathlib import Path


def tokens(text):
    return re.findall(r"\w+", unicodedata.normalize('NFC', text).lower())


def edit_distance(reference, hypothesis):
    previous = list(range(len(hypothesis) + 1))
    for i, left in enumerate(reference, 1):
        current = [i]
        for j, right in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (left != right)))
        previous = current
    return previous[-1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report')
    parser.add_argument('--model',default='small')
    parser.add_argument('--model-cache',default='/tmp/local-tts-eval-models')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    from faster_whisper import WhisperModel
    model=WhisperModel(args.model,device='cpu',compute_type='int8',cpu_threads=6,
                       download_root=args.model_cache)
    report=json.loads(Path(args.report).read_text())
    rows=report['samples']
    result={'recognizer':'faster-whisper','model':args.model,'compute_type':'int8',
            'note':'No reference text supplied to recognizer. ASR is not a pronunciation or naturalness verdict.',
            'samples':[]}
    for row in rows:
        if row.get('error'): continue
        segments,_=model.transcribe(row['path'],language=row['language'].split('-')[0],
                                    beam_size=5,temperature=0,condition_on_previous_text=False,
                                    vad_filter=False)
        hypothesis=' '.join(segment.text.strip() for segment in segments)
        reference=tokens(row['text']); heard=tokens(hypothesis)
        errors=edit_distance(reference,heard)
        reference_chars=''.join(reference); heard_chars=''.join(heard)
        char_errors=edit_distance(reference_chars,heard_chars)
        record=dict(row,transcript=hypothesis,word_errors=errors,reference_words=len(reference),
                    wer=errors/max(1,len(reference)),char_errors=char_errors,
                    reference_chars=len(reference_chars),cer=char_errors/max(1,len(reference_chars)))
        result['samples'].append(record)
        Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(row.get('variant',row['provider']),row.get('sample',''),errors,'/',len(reference),hypothesis,flush=True)

if __name__=='__main__': main()
