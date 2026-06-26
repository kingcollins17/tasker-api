-- 1. Insert Service Categories
INSERT INTO categories (
    id, 
    name, 
    description, 
    is_active, 
    created_at, 
    updated_at
) VALUES 
('c101a111-2222-3333-4444-555555555551', 'Home Improvement & Maintenance', 'Services related to repairing, improving, or maintaining homes and property.', TRUE, NOW(), NOW()),
('c101a111-2222-3333-4444-555555555552', 'Cleaning & Housekeeping', 'Professional cleaning services for residential and commercial spaces.', TRUE, NOW(), NOW()),
('c101a111-2222-3333-4444-555555555553', 'Professional & Tech Services', 'Specialized professional, IT, and administrative services.', TRUE, NOW(), NOW())
ON CONFLICT (name) DO NOTHING;

-- 2. Insert Services
INSERT INTO services (
    id, 
    name, 
    take_rate, 
    is_active, 
    category_id, 
    created_at, 
    updated_at
) VALUES 
-- Home Improvement & Maintenance Services
('s202b222-1111-2222-3333-444444444401', 'Carpentry', 0.10, TRUE, 'c101a111-2222-3333-4444-555555555551', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444402', 'Plumbing', 0.12, TRUE, 'c101a111-2222-3333-4444-555555555551', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444403', 'Electrical Work', 0.12, TRUE, 'c101a111-2222-3333-4444-555555555551', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444404', 'HVAC Maintenance', 0.10, TRUE, 'c101a111-2222-3333-4444-555555555551', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444405', 'Painting', 0.08, TRUE, 'c101a111-2222-3333-4444-555555555551', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444406', 'Gardening & Landscaping', 0.10, TRUE, 'c101a111-2222-3333-4444-555555555551', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444407', 'Appliance Repair', 0.10, TRUE, 'c101a111-2222-3333-4444-555555555551', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444408', 'Pest Control', 0.10, TRUE, 'c101a111-2222-3333-4444-555555555551', NOW(), NOW()),

-- Cleaning & Housekeeping Services
('s202b222-1111-2222-3333-444444444409', 'Deep House Cleaning', 0.15, TRUE, 'c101a111-2222-3333-4444-555555555552', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444410', 'Carpet Cleaning', 0.10, TRUE, 'c101a111-2222-3333-4444-555555555552', NOW(), NOW()),
('s202b222-1111-2222-3333-444444444411', 'Window Washing', 0.08, TRUE, 'c101a111-2222-3333-4444-555555555552', NOW(), NOW()),

-- Professional & Tech Services
('s202b222-1111-2222-3333-444444444412', 'IT Support & Setup', 0.10, TRUE, 'c101a111-2222-3333-4444-555555555553', NOW(), NOW())
ON CONFLICT (name) DO NOTHING;
