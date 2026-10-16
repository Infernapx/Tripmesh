import React from 'react';

export default function GlobeAirplaneOverlay() {
  return (
    <div
      className="relative w-[380px] h-[380px] flex items-center justify-center pointer-events-none select-none"
      aria-hidden="true"
    >
      {/* 
        Subtle Ambient Atmospheric Glow around the Globe Area
        Reinforces sunset rim light and ocean reflection without any movement
      */}
      <div
        className="absolute w-[360px] h-[360px] rounded-full pointer-events-none opacity-35"
        style={{
          background: 'radial-gradient(circle at 60% 40%, rgba(255,154,85,0.25) 0%, rgba(168,85,247,0.18) 40%, rgba(40,223,255,0.2) 70%, transparent 80%)',
          filter: 'blur(30px)',
        }}
      />

      {/* Orbiting Airplane & Glowing Dotted Flight Path */}
      <div className="relative w-[360px] h-[360px] flex items-center justify-center">
        {/* Glowing Dotted Flight Path SVG */}
        <svg
          className="absolute inset-0 w-full h-full pointer-events-none overflow-visible"
          viewBox="0 0 360 360"
        >
          <ellipse
            cx="180"
            cy="180"
            rx="155"
            ry="78"
            fill="none"
            stroke="#28DFFF"
            strokeWidth="2.2"
            strokeDasharray="4 8"
            className="opacity-75"
            style={{
              filter: 'drop-shadow(0 0 5px rgba(40, 223, 255, 0.85)) drop-shadow(0 0 12px rgba(118, 103, 255, 0.45))',
              transform: 'rotate(-14deg)',
              transformOrigin: 'center',
            }}
          />
        </svg>

        {/* Orbiting Mini Airplane: ONLY the airplane moves along its loop */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            transform: 'rotate(-14deg)',
            transformOrigin: 'center',
          }}
        >
          <div
            className="absolute top-1/2 left-1/2 w-11 h-11 -ml-5.5 -mt-5.5"
            style={{
              animation: 'orbitAirplaneLoop 14s linear infinite',
            }}
          >
            <img
              src="/assets/mini_airplane.png"
              alt="Miniature Passenger Airplane"
              className="w-full h-full object-contain drop-shadow-[0_4px_10px_rgba(40,223,255,0.7)] drop-shadow-[0_6px_14px_rgba(255,154,85,0.4)]"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
