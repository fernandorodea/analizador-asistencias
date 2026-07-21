self.addEventListener('install', (event) => {
    console.log('SIAGE: Service Worker instalado correctamente.');
});

self.addEventListener('fetch', (event) => {
    // Esto permite que el portal siga funcionando de manera normal en Render
});
