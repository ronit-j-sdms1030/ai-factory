// Compile-check generated screens, so a broken one never reaches a reviewer.
//
// The preview compiles each screen in the browser with Babel standalone and
// marks the failures, but that happens after publication — by then the screen
// is committed, the pull request is open and the pipeline has reported
// success. This runs the same parse server-side, while the agent can still do
// something about it.
//
// Reads {"name": "<source>", ...} on stdin, writes {"name": "<error>" | null}
// on stdout. Never throws for a bad screen: a screen that will not parse is
// the output, not an exception.

const babel = require('@babel/core');

let raw = '';
process.stdin.on('data', (chunk) => { raw += chunk; });
process.stdin.on('end', () => {
  let sources;
  try {
    sources = JSON.parse(raw);
  } catch (err) {
    process.stdout.write(JSON.stringify({ __error: 'could not parse input: ' + err.message }));
    return;
  }

  const results = {};
  for (const [name, source] of Object.entries(sources)) {
    try {
      // The classic runtime, matching the preview. The automatic runtime
      // injects `import { jsx } from "react/jsx-runtime"` into its own
      // output, which the preview's eval then rejects — checking against a
      // different runtime here would pass screens the preview cannot run.
      babel.transformSync(source, {
        presets: [['@babel/preset-react', { runtime: 'classic' }]],
        filename: name + '.jsx',
        babelrc: false,
        configFile: false,
      });
      results[name] = null;
    } catch (err) {
      // First line only. The rest is a code frame that would bloat the
      // retry prompt without telling the model anything the message does not.
      results[name] = String(err.message).split('\n')[0];
    }
  }
  process.stdout.write(JSON.stringify(results));
});
