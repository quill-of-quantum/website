// 修正版：兼容 WebGL2 + 树莓派 GPU，添加 sampler3D 精度声明
THREE.VolumeShader = {

    uniforms: {
        "map": { value: null },
        "steps": { value: 256.0 },
        "alphaCorrection": { value: 0.5 }
    },

    vertexShader: `
        varying vec3 vWorldPosition;
        void main() {
            vWorldPosition = position.xyz * 0.5 + 0.5;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
    `,

    fragmentShader: `
        precision highp float;
        precision highp sampler3D;

        uniform highp sampler3D map;
        uniform float steps;
        uniform float alphaCorrection;
        varying vec3 vWorldPosition;

        void main() {
            vec3 rayDir = normalize(vWorldPosition - vec3(0.5));
            vec3 rayOrigin = vec3(0.5);
            vec4 sum = vec4(0.0);
            
            for (float t = 0.0; t < 1.0; t += 1.0 / steps) {
                vec3 p = rayOrigin + rayDir * t;
                vec4 c = texture(map, p);
                c.a = pow(c.a, alphaCorrection);
                sum.rgb += (1.0 - sum.a) * c.a * c.rgb;
                sum.a += (1.0 - sum.a) * c.a;
                if (sum.a > 0.95) break;
            }

            gl_FragColor = sum;
        }
    `
};
