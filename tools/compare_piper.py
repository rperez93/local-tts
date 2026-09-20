"""Test existing Piper synthesis as input to selected RVC models. No preference writes."""
import argparse
import json,time
from pathlib import Path
from localtts import config,providers,text
from localtts.calibration import SOUND_SAMPLES,measure_wav

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output')
    parser.add_argument('--model-directory', required=True)
    args=parser.parse_args()
    work=Path(args.output);work.mkdir(parents=True,exist_ok=False)
    report={'samples':[]};cfg=config.load()
    def record(lang,sample,sentence,variant,path,seconds):
     report['samples'].append(dict(language=lang,sample=sample,text=sentence,variant=variant,
      path=str(path.resolve()),provider='piper' if variant.endswith('-base') else 'rvc',
      render_s=seconds,**measure_wav(str(path))))
     (work/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
     print(lang,sample,variant,flush=True)
    for name in ['es_MX-claude-high','es_AR-daniela-high']:
     for sample,sentence in SOUND_SAMPLES['es']:
      model=str(Path(args.model_directory)/(name+'.onnx'))
      base=providers.build('piper',cfg,lang='es').with_settings({'model':model})
      path=work/(name+'-'+sample+'-base.wav');start=time.perf_counter()
      text.synthesize_chunked(base,sentence,str(path));record('es',sample,sentence,name+'-base',path,time.perf_counter()-start)
      out=work/(name+'-'+sample+'-rvc.wav');rvc=providers.build('rvc',cfg,lang='es');start=time.perf_counter()
      rvc._convert_via_server(rvc.settings['server_url'],str(path),str(out));record('es',sample,sentence,name+'-rvc',out,time.perf_counter()-start)
    for sample,sentence in SOUND_SAMPLES['en']:
     base=providers.build('piper',cfg,lang='en');path=work/('en-'+sample+'-base.wav');start=time.perf_counter()
     text.synthesize_chunked(base,sentence,str(path));record('en',sample,sentence,'lessac-base',path,time.perf_counter()-start)
     out=work/('en-'+sample+'-rvc.wav');rvc=providers.build('rvc',cfg,lang='en');start=time.perf_counter()
     rvc._convert_via_server(rvc.settings['server_url'],str(path),str(out));record('en',sample,sentence,'lessac-rvc',out,time.perf_counter()-start)
    print(work/'report.json',flush=True)

if __name__=='__main__': main()
