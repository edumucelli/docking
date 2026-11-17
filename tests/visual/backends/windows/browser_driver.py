"""Fixture requests shared by native browser acceptance tools."""

import json
import threading


def browser_script(browser, call, source):
    """Read one WebView2 script result through its GTK-thread callback."""
    done, results = threading.Event(), []

    def completed(text):
        results.append(json.loads(text))
        done.set()

    call(lambda: browser._engine.execute(source, completed))
    assert done.wait(20), "Browser script timed out"
    return results[0]


def worker_request(script, wait_for, action, title=None):
    """Wait for the shared page fixture's asynchronous service-worker reply."""
    script(
        "window.fixture.workerReply=null;window.fixture.workerRequest("
        + json.dumps(action)
        + ","
        + json.dumps({"title": title})
        + ").then(rows=>window.fixture.workerReply={ok:true,rows})"
        ".catch(e=>window.fixture.workerReply={ok:false,error:e.message});true"
    )
    answer = wait_for(
        lambda: script("window.fixture.workerReply"), "No worker reply", 25
    )
    assert answer["ok"], answer
    return answer["rows"]
