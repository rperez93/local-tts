"""Render a controlled local Spanish/English sound sweep; no playback or config writes.

Each converted variant uses the exact same source WAV as its control. Run with
PYTHONPATH=src python tools/run_sound_sweep.py OUTPUT_DIRECTORY.
"""
import copy
import json
import os
import sys
import time
from pathlib import Path
from localtts import config, providers, text
from localtts.calibration import SOUND_SAMPLES, measure_wav


def main():
    work=Path(sys.argv[1]); work.mkdir(parents=True,exist_ok=False)
    cfg=config.load()
    report={'note':'Unattended controlled tests; audio metrics and ASR do not measure naturalness.', 'samples':[]}
    def save(lang,sample,sentence,variant,path,elapsed,settings):
        row=dict(language=lang,sample=sample,text=sentence,variant=variant,path=str(path.resolve()),
                 provider='kokoro' if variant.startswith('base') else 'rvc',
                 render_s=elapsed,settings=settings,**measure_wav(str(path)))
        report['samples'].append(row)
        (work/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(lang,sample,variant,'%.2fs'%elapsed,flush=True)
    for lang in ['es','en','en-US']:
        for sample,sentence in SOUND_SAMPLES[lang.split('-')[0]]:
            rvc=providers.build('rvc',cfg,lang=lang)
            marks=int(rvc.delivery()['emphasis_lengthen']) if lang!='en-US' else 0
            for setting,emphasis in [('current',marks)]+([('neutral',0)] if lang=='es' and marks else []):
                base=providers.build('kokoro',cfg,lang=lang).with_settings({'emphasis_lengthen':emphasis})
                path=work/('%s-%s-base-%s.wav'%(lang,sample,setting))
                spoken=text.apply_pronunciations(sentence,cfg.get('pronunciations'),lang)
                started=time.perf_counter(); text.synthesize_chunked(base,spoken,str(path))
                save(lang,sample,sentence,'base-'+setting,path,time.perf_counter()-started,{'emphasis_lengthen':emphasis,'voice':base.resolved_voice()})
                variants=[('rvc-'+setting,{})] if lang!='en-US' else []
                if lang=='es' and setting=='current':
                    variants += [('rvc-index065',{'index_rate':.65}),('rvc-index035',{'index_rate':.35})]
                for variant,overrides in variants:
                    provider=rvc.with_settings({'conversion':{lang:overrides}}) if overrides else rvc
                    out=work/('%s-%s-%s.wav'%(lang,sample,variant))
                    started=time.perf_counter()
                    provider._convert_via_server(provider.settings['server_url'],str(path),str(out))
                    save(lang,sample,sentence,variant,out,time.perf_counter()-started,
                         {'emphasis_lengthen':emphasis,'conversion_overrides':overrides,'source':str(path.resolve()),'model':provider.server_model_name()})
    print(work/'report.json',flush=True)

if __name__=='__main__': main()
