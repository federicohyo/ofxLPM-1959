#include "ofMain.h"
#include "ofApp.h"

// One window, one process. At the venue there is exactly one thing to start:
// the left half is the die's own membrane, the right half is the 1959 film that
// is driving it. setup() applies "fullscreen" from settings.json.
int main() {
    ofGLWindowSettings s;
    s.setSize(1920, 1080);
    s.windowMode = OF_WINDOW;
    s.title = "1959";
    ofCreateWindow(s);
    ofRunApp(new ofApp());
}
