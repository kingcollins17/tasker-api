-- 1. Insert Service Categories (Delivery, Cleaning, Plumbing, Moving)
INSERT INTO categories (
    id, 
    name, 
    description,
    default_base_price,
    default_duration_min,
    per_km_rate,
    per_minute_rate,
    is_active, 
    created_at, 
    updated_at
) VALUES 
('c101a111-2222-3333-4444-555555555501', 'Delivery & Courier', 'Fast and reliable courier, errand, and parcel delivery services.', 1000.0, 30, 150.0, 10.0, TRUE, NOW(), NOW()),
('c101a111-2222-3333-4444-555555555502', 'Cleaning & Housekeeping', 'Professional residential, commercial, and deep cleaning services.', 3000.0, 120, 100.0, 25.0, TRUE, NOW(), NOW()),
('c101a111-2222-3333-4444-555555555503', 'Plumbing Services', 'Expert plumbing installation, leak repairs, and pipe maintenance.', 4000.0, 90, 120.0, 30.0, TRUE, NOW(), NOW()),
('c101a111-2222-3333-4444-555555555504', 'Moving & Hauling', 'Local home moving, office relocation, and heavy furniture hauling.', 5000.0, 180, 200.0, 35.0, TRUE, NOW(), NOW())
ON CONFLICT (name) DO UPDATE SET
    description = EXCLUDED.description,
    default_base_price = EXCLUDED.default_base_price,
    default_duration_min = EXCLUDED.default_duration_min,
    per_km_rate = EXCLUDED.per_km_rate,
    per_minute_rate = EXCLUDED.per_minute_rate,
    updated_at = NOW();

-- 2. Insert Services per Category (5 services each)
INSERT INTO services (
    id, 
    name, 
    base_price,
    default_duration_min,
    per_km_rate,
    per_minute_rate,
    take_rate, 
    is_active, 
    category_id, 
    created_at, 
    updated_at
) VALUES 
-- Delivery & Courier Services
('s202b222-1111-2222-3333-444444444101', 'Express Parcel Courier', 1000.0, 30, 150.0, 10.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555501', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444102', 'Grocery & Shopping Errand', 1500.0, 45, 150.0, 15.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555501', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444103', 'Document & Contract Delivery', 1200.0, 30, 150.0, 10.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555501', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444104', 'Food & Catering Delivery', 1000.0, 30, 150.0, 10.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555501', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444105', 'Heavy Package Freight Delivery', 3000.0, 60, 200.0, 20.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555501', NOW(), NOW()),

-- Cleaning & Housekeeping Services
('s202b222-1111-2222-3333-444444444201', 'Standard Home Cleaning', 3000.0, 120, 100.0, 25.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555502', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444202', 'Deep House Cleaning', 6000.0, 240, 100.0, 30.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555502', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444203', 'Office & Commercial Cleaning', 5000.0, 180, 100.0, 25.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555502', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444204', 'Carpet & Upholstery Cleaning', 4000.0, 120, 100.0, 25.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555502', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444205', 'Window & Glass Washing', 2500.0, 90, 100.0, 20.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555502', NOW(), NOW()),

-- Plumbing Services
('s202b222-1111-2222-3333-444444444301', 'Leak Repair & Pipe Fixing', 4000.0, 90, 120.0, 30.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555503', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444302', 'Drain Unclogging & Cleaning', 3500.0, 60, 120.0, 30.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555503', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444303', 'Tap & Sink Fixture Installation', 3000.0, 60, 120.0, 25.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555503', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444304', 'Water Heater Repair & Installation', 5000.0, 120, 120.0, 35.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555503', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444305', 'Toilet & Bathroom Plumbing', 4500.0, 90, 120.0, 30.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555503', NOW(), NOW()),

-- Moving & Hauling Services
('s202b222-1111-2222-3333-444444444401', 'Apartment Local Moving', 8000.0, 240, 200.0, 40.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555504', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444402', 'Heavy Furniture Relocation', 5000.0, 150, 200.0, 35.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555504', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444403', 'Office & Equipment Moving', 10000.0, 300, 250.0, 45.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555504', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444404', 'Packing & Unpacking Helper', 3500.0, 120, 150.0, 25.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555504', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444405', 'Junk & Debris Removal', 4000.0, 90, 180.0, 30.0, 0.15, TRUE, 'c101a111-2222-3333-4444-555555555504', NOW(), NOW())
ON CONFLICT (name) DO UPDATE SET
    base_price = EXCLUDED.base_price,
    default_duration_min = EXCLUDED.default_duration_min,
    per_km_rate = EXCLUDED.per_km_rate,
    per_minute_rate = EXCLUDED.per_minute_rate,
    take_rate = EXCLUDED.take_rate,
    category_id = EXCLUDED.category_id,
    updated_at = NOW();
