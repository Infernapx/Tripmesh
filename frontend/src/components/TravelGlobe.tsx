import React, { useState, useEffect, useRef } from 'react';

export default function TravelGlobe() {
  const [mouseOffset, setMouseOffset] = useState({ x: 0, y: 0 });
  const [isHovered, setIsHovered] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Parallax tracking with gentle damping (8-12px max)
    const handleMouseMove = (e: MouseEvent) => {
      const centerX = window.innerWidth / 2;
      const centerY = window.innerHeight / 2;
      const offsetX = ((e.clientX - centerX) / centerX) * 11;
      const offsetY = ((e.clientY - centerY) / centerY) * 10;
      setMouseOffset({ x: offsetX, y: offsetY });
    };

    window.addEventListener('mousemove', handleMouseMove, { passive: true });
    return () => window.removeEventListener('mousemove', handleMouseMove);
  }, []);

  return (
    <div
      ref={containerRef}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      className="relative flex items-center justify-center select-none cursor-pointer group"
      style={{
        transform: `translate3d(${mouseOffset.x}px, ${mouseOffset.y}px, 0)`,
        transition: 'transform 0.25s cubic-bezier(0.2, 0.8, 0.2, 1)',
      }}
      aria-label="Interactive TripMesh 3D Travel Globe"
    >
      {/* 
        LAYER 1: Atmospheric Sunset & Ocean Rim Glow behind Globe 
        Matches the Mediterranean sunset (warm peach on right, cyan on ocean bottom)
      */}
      <div
        className={`absolute rounded-full pointer-events-none transition-all duration-700 ${
          isHovered
            ? 'w-[560px] h-[560px] opacity-75 scale-105'
            : 'w-[500px] h-[500px] opacity-55'
        }`}
        style={{
          background: 'radial-gradient(circle at 65% 45%, rgba(255,155,88,0.45) 0%, rgba(212,92,255,0.3) 35%, rgba(36,217,255,0.35) 60%, rgba(6,26,58,0) 80%)',
          filter: 'blur(38px)',
          zIndex: 1,
        }}
      />

      {/* 
        LAYER 2: Distant Drift Clouds Behind the Globe 
      */}
      <div
        className="absolute -top-6 -left-12 w-[460px] h-[180px] pointer-events-none opacity-40 z-[2]"
        style={{
          backgroundImage: 'url(/assets/floating_clouds.png)',
          backgroundSize: 'contain',
          backgroundRepeat: 'no-repeat',
          animation: 'cloudDriftSlow 20s ease-in-out infinite alternate',
        }}
      />

      {/* 
        LAYER 3: 3D Travel Globe (Enlarged 25-35%, with Sunset Rim Lighting)
      */}
      <div
        className="relative z-[3] w-[370px] h-[370px] sm:w-[440px] sm:h-[440px] lg:w-[490px] lg:h-[490px] xl:w-[530px] xl:h-[530px] flex items-center justify-center transition-transform duration-500"
        style={{
          animation: 'globeFloat 7s ease-in-out infinite',
          transform: isHovered ? 'scale(1.035)' : 'scale(1)',
        }}
      >
        <img
          src="/assets/travel_globe.png"
          alt="Magical 3D Travel Globe with World Landmarks"
          className="w-full h-full object-contain"
          style={{
            filter: `
              drop-shadow(16px -8px 30px rgba(255, 155, 88, 0.48))
              drop-shadow(-12px 14px 26px rgba(36, 217, 255, 0.42))
              drop-shadow(0 25px 45px rgba(6, 26, 58, 0.75))
            `,
          }}
          loading="eager"
          decoding="async"
        />

        {/* Orbit Flight Trail SVG */}
        <svg
          className="absolute inset-0 w-full h-full pointer-events-none overflow-visible z-[4]"
          viewBox="0 0 500 500"
        >
          <ellipse
            cx="250"
            cy="245"
            rx="235"
            ry="115"
            fill="none"
            stroke="#24D9FF"
            strokeWidth="2.5"
            strokeDasharray="5 9"
            className="opacity-80"
            style={{
              filter: 'drop-shadow(0 0 8px rgba(36, 217, 255, 0.85)) drop-shadow(0 0 16px rgba(131, 103, 255, 0.5))',
              transform: 'rotate(-15deg)',
              transformOrigin: 'center',
            }}
          />
        </svg>

        {/* Orbiting Mini Airplane */}
        <div
          className="absolute inset-0 pointer-events-none z-[5]"
          style={{
            transform: 'rotate(-15deg)',
            transformOrigin: 'center',
          }}
        >
          <div
            className="absolute top-1/2 left-1/2 w-14 h-14 -ml-7 -mt-7 transition-all"
            style={{
              animation: `floatAirplane ${isHovered ? '9.5s' : '12.5s'} linear infinite`,
            }}
          >
            <img
              src="/assets/mini_airplane.png"
              alt="Miniature Passenger Airplane"
              className="w-full h-full object-contain drop-shadow-[0_4px_14px_rgba(36,217,255,0.85)] drop-shadow-[0_8px_20px_rgba(255,155,88,0.5)]"
            />
          </div>
        </div>

        {/* 
          LAYER 4: Foreground Fluffy Clouds overlapping lower curve of Globe 
        */}
        <div
          className="absolute -bottom-6 -left-4 w-[340px] h-[130px] pointer-events-none z-[6] opacity-90"
          style={{
            backgroundImage: 'url(/assets/floating_clouds.png)',
            backgroundSize: 'contain',
            backgroundRepeat: 'no-repeat',
            animation: 'cloudDriftSlow 16s ease-in-out infinite alternate',
          }}
        />
      </div>

      {/* 
        LAYER 5: Wide Sunset Cloud Bank extending across the base toward the center
        Visually bridges the globe with the central ocean and sunset scene!
      */}
      <div
        className="absolute -bottom-10 -left-10 sm:-left-16 lg:-right-36 w-[560px] sm:w-[680px] lg:w-[780px] h-[190px] pointer-events-none z-[4] opacity-85"
        style={{
          backgroundImage: 'url(/assets/sunset_cloud_shelf.png)',
          backgroundSize: 'contain',
          backgroundRepeat: 'no-repeat',
          backgroundPosition: 'left bottom',
          animation: 'cloudDriftSlow 22s ease-in-out infinite alternate-reverse',
        }}
      />
    </div>
  );
}
