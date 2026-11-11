; AutoLISP reference: geometric utilities
(princ (distance '(0 0) '(3 4)))
(princ (rtos (angle '(0 0) '(0 5)) 2 4))
(princ (polar '(0 0) 0 10))
(princ (inters '(0 0) '(10 10) '(0 10) '(10 0)))
(princ (rtos (* 180 (/ (angle '(0 0) '(1 1)) pi)) 2 0))
