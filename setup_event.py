"""Create/update the AWS resources an event needs, from events/<name>.toml:
Transcribe custom vocabularies <id>-en / <id>-ko and Amazon Translate terminologies <id>-en / <id>-ko.

  .venv/bin/python setup_event.py [event] [--region ap-northeast-2] [--dry-run]
"""
import argparse
import time

import boto3

import event as event_config


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("event", nargs="?", default=None)
    ap.add_argument("--region", default="ap-northeast-2")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--dry-run", action="store_true", help="print what would be created")
    a = ap.parse_args()
    ev = event_config.load(a.event)
    if a.dry_run:
        for lang in ev.languages:
            ph = ev.phrases(lang)
            print(f"vocabulary {ev.vocabulary(lang)}: {len(ph)} phrases, {sum(len(p.encode()) + 1 for p in ph)} bytes"
                  f" (limit {event_config.VOCABULARY_BYTES}): {ph}")
            print(f"terminology {ev.terminology(lang)}:\n{ev.terminology_csv(lang)}")
        print(ev.prompt("en", "ko", 34))
        return
    session = boto3.Session(profile_name=a.profile, region_name=a.region)
    tc = session.client("transcribe")
    for lang in ev.languages:
        name, phrases = ev.vocabulary(lang), ev.phrases(lang)
        kw = dict(VocabularyName=name, LanguageCode=event_config.TRANSCRIBE_LANG[lang], Phrases=phrases)
        try:
            tc.get_vocabulary(VocabularyName=name)
            tc.update_vocabulary(**kw)
            print(f"updating vocabulary {name} ({len(phrases)} phrases)")
        except tc.exceptions.BadRequestException:  # Transcribe reports a missing vocabulary as BadRequest
            tc.create_vocabulary(**kw)
            print(f"creating vocabulary {name} ({len(phrases)} phrases)")
    tr = session.client("translate")
    for lang in ev.languages:
        tr.import_terminology(Name=ev.terminology(lang), MergeStrategy="OVERWRITE",
                              TerminologyData={"File": ev.terminology_csv(lang).encode(), "Format": "CSV", "Directionality": "UNI"})
        print(f"terminology {ev.terminology(lang)} imported")
    pending = {ev.vocabulary(l) for l in ev.languages}
    while pending:
        time.sleep(10)
        for name in sorted(pending):
            st = tc.get_vocabulary(VocabularyName=name)
            if st["VocabularyState"] != "PENDING":
                print(f"vocabulary {name}: {st['VocabularyState']} {st.get('FailureReason', '')}")
                pending.discard(name)


if __name__ == "__main__":
    main()
