/* pixi-live2d-display's UMD bundle reads process.env.NODE_ENV at load time.
   Web pages have no `process`, which aborts evaluation before the library
   finishes registering. This shim only supplies the build flag. */
window.process = window.process || { env: { NODE_ENV: "production" } };
