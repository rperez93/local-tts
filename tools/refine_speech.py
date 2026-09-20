"""Second controlled pass; writes audio/reports, never changes user settings."""
import argparse
import copy
import importlib.util
import json
import sys
import time
from pathlib import Path
from localtts import config,providers,text
from localtts.calibration import SOUND_SAMPLES,measure_wav


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output')
    parser.add_argument('--baseline', required=True, help='First sound-sweep report.json')
    parser.add_argument('--kokoro-script', required=True, help='Installed Kokoro server script')
    args=parser.parse_args()
    work=Path(args.output);work.mkdir(parents=True,exist_ok=False)
    cfg=config.load(); report={'samples':[]}
    spec=importlib.util.spec_from_file_location('local_kokoro_server',args.kokoro_script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    def record(lang,sample,sentence,variant,path,settings):
        report['samples'].append(dict(language=lang,sample=sample,text=sentence,variant=variant,
              path=str(path.resolve()),provider='rvc',settings=settings,**measure_wav(str(path))))
        (work/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(lang,sample,variant,flush=True)
    for sample,sentence in SOUND_SAMPLES['es']:
        if sample not in ('r_rr','r_contrasts','vowels','stops'):continue
        for variant,settings in [('speed090',{'speed':.9}),('trill_long',{}),('base_alex',{})]:
            candidate=copy.deepcopy(cfg)
            if variant=='trill_long':
                ipa=module.phonemes(sentence,'es').replace('r','rː').rstrip('.,;:!?… ')
                candidate.setdefault('pronunciations',{})['es:'+sentence]='/'+ipa+'/'
            base=providers.build('kokoro',candidate,lang='es').with_settings(dict(emphasis_lengthen=2,**settings))
            source=work/('%s-%s-source.wav'%(sample,variant));out=work/('%s-%s.wav'%(sample,variant))
            voice='em_alex' if variant=='base_alex' else None
            text.synthesize_chunked(base,sentence,str(source),voice=voice)
            converter=providers.build('rvc',candidate,lang='es')
            converter._convert_via_server(converter.settings['server_url'],str(source),str(out))
            record('es',sample,sentence,variant,out,dict(base_settings=base.settings,voice=voice))
    source_report=json.loads(Path(args.baseline).read_text())
    for row in source_report['samples']:
        if row['language']!='en' or row['variant']!='base-current':continue
        for variant,overrides in [('index065',{'index_rate':.65}),('index035',{'index_rate':.35}),('protect0',{'protect':0})]:
            converter=providers.build('rvc',cfg,lang='en').with_settings({'conversion':{'en':overrides}})
            out=work/('en-%s-%s.wav'%(row['sample'],variant))
            converter._convert_via_server(converter.settings['server_url'],row['path'],str(out))
            record('en',row['sample'],row['text'],variant,out,overrides)
    print(work/'report.json',flush=True)

if __name__=='__main__':main()
