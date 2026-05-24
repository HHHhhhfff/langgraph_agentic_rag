

<!-- source: data\demo_docs\2412.16030_Non-stationary_Aharonov-Bohm_effect.pdf -->

# Non-stationary Aharonov-Bohm effect

A. I. Milstein $^{1,2,*}$ and I. S. Terekhov $^{3,\dagger}$

$^{1}$ Budker Institute of Nuclear Physics of SB RAS, 630090 Novosibirsk, Russia

$^{2}$ Novosibirsk State University, 630090 Novosibirsk, Russia

$^{3}$ School of Physics and Engineering, ITMO University, 197101 St. Petersburg, Russia

(Dated: December 23, 2024)

The non-stationary Aharonov-Bohm effect (scattering of electron in the field of a narrow solenoid with alternating current) is considered. Using the eikonal approximation, the wave function of electron, the differential and total scattering cross sections are found. Unlike the case of direct current, the total cross section in the case of alternating current turns out to be finite. An oscillating asymmetry in the differential scattering cross section is discovered. The possibility of experimental observation of the effect is discussed.

# I. INTRODUCTION

The scattering of an electron on a narrow long solenoid with a direct current, the Aharonov-Bohm effect $[1]$ , is one of the striking manifestations of quantum mechanics. The change of the scattering cross section with a change of the magnetic flux in the region inaccessible to the electron motion is surprising. Many papers have been devoted to the study of the Aharonov-Bohm effect (see, for example, the review $[2]$ ). A distinctive feature of the Aharonov-Bohm effect is the topology of space accessible to the electron motion (punctured line in a space). If a direct current is passed through the solenoid, then, by virtue of Maxwell equations, the electric and magnetic fields outside the solenoid will be equal to zero.

However, if the current in the solenoid is alternating, the electromagnetic fields will be non-zero in the accessible to the electron motion region. In this case, the topology of the space accessible to the electron motion is the same as that in the stationary case, so that the influence of alternating magnetic field flux inside the solenoid on the electron motion outside the solenoid (the non-stationary Aharonov-Bohm effect) will be somewhat similar to the stationary effect, but will also have some differences. The non-stationary Aharonov-Bohm effect has been discussed in many papers with contradictory results (see, for example, $[3–8]$ ). However, none of these papers calculated such experimentally important quantities as the differential and total scattering cross sections. Our work is devoted to the study of these quantities.

# II. EIKONAL APPROXIMATION AND THE CROSS SECTION OF NON-STATIONARY AHARONOV-BOHM EFFECT

Let us consider an infinitely long solenoid with radius $a$ and the number $n$ of turns per unit length, through which a current $I(t) = I_0 \cos(\omega t)$ is passed. We denote

$$
\Phi_ {0} = \frac {4 \pi^ {2} a ^ {2}}{c} n I _ {0}, \tag {1}
$$

where c is the speed of light. At $\omega = 0$ this quantity coincides with the magnetic field flux through the solenoid. We are interested in the limit $a \rightarrow 0$ at a fixed value of $\Phi_{0}$ . Solving Maxwell equation, we find the vector-potential $\boldsymbol{A}(\boldsymbol{r}, t)$ , the magnetic field $\boldsymbol{B}(\boldsymbol{r}, t)$ and the electric field $\boldsymbol{E}(\boldsymbol{r}, t)$ outside the solenoid,

$$
\boldsymbol {A} (\boldsymbol {r}, t) = [ \boldsymbol {\nu} \times \boldsymbol {n} ] A (r, t), \boldsymbol {B} (\boldsymbol {r}, t) = \boldsymbol {\nu} B (r, t),
$$

$$
\boldsymbol {E} (\boldsymbol {r}, t) = [ \boldsymbol {\nu} \times \boldsymbol {n} ] E (r, t),
$$

$$
A (r, t) = \Phi_ {0} \frac {k}{4} [ J _ {1} (k r) \sin (\omega t) - N _ {1} (k r) \cos (\omega t) ],
$$

$$
B (r, t) = \Phi_ {0} \frac {k ^ {2}}{4} [ - J _ {0} (k r) \sin (\omega t) + N _ {0} (k r) \cos (\omega t) ],
$$

$$
E (r, t) = - \Phi_ {0} \frac {k ^ {2}}{4} [ J _ {1} (k r) \cos (\omega t) + N _ {1} (k r) \sin (\omega t) ], \tag {2}
$$

where $\pmb{\nu}$ is a unit vector directed along the z-axis and parallel to the solenoid axis, $\pmb{n} = \pmb{r}/r$ , $\pmb{r} = (x, y, 0)$ , $r = \sqrt{x^{2} + y^{2}}$ , $k = \omega/c$ , $J_{l}(x)$ is the Bessel function, $N_{l}(x)$ is the Neumann function.

For $kr \ll 1$ we have

$$
A (r, t) = \frac {\Phi_ {0}}{2 \pi r} \cos (\omega t),
$$

$$
B (r, t) = k ^ {2} \frac {\Phi_ {0}}{2 \pi} \ln (k r) \cos (\omega t),
$$

$$
E (r, t) = k \frac {\Phi_ {0}}{2 \pi r} \sin (\omega t). \tag {3}
$$

For $kr \gg 1$ the asymptotics of fields are

$$
A (r, t) = \Phi_ {0} \sqrt {\frac {k}{8 \pi r}} \cos (\omega t - k r + \pi / 4),
$$

$$
B (r, t) = - \Phi_ {0} k \sqrt {\frac {k}{8 \pi r}} \sin (\omega t - k r + \pi / 4),
$$

$$
E (r, t) = k \Phi_ {0} \sqrt {\frac {k}{8 \pi r}} \sin (\omega t - k r + \pi / 4). \tag {4}
$$

These asymptotics correspond to the radiation field of a solenoid.

We consider a non-relativistic problem, so that we assume that the electron velocity v satisfies the condition $v/c \ll 1$ . The Pauli equation for the wave function $\psi(\boldsymbol{r}, t)$ of an electron in the field of a solenoid reads

$$
\begin{array}{l} i \hbar \frac {\partial \psi (\pmb {r} , t)}{\partial t} = \left[ \frac {1}{2 M} \left(\pmb {p} - \frac {e}{c} [ \pmb {\nu} \times \pmb {n} ] A (r, t)\right) ^ {2} \right. \\ \left. - \lambda \mu B (r, t) \right] \psi (\boldsymbol {r}, t), \tag {5} \\ \end{array}
$$

where M, e and $\mu$ are the mass, charge and magnetic moment of electron, $\lambda = \pm1$ corresponds to the projection $\pm1/2$ of spin onto the z axis. Since $A(r,t)$ and $B(r,t)$ are periodic functions in t with the period $T = 2\pi/\omega$ , the wave function satisfies the periodicity condition [9, 10],

$$
\psi (\boldsymbol {r}, t + T) = e ^ {i \alpha T} \psi (\boldsymbol {r}, t). \tag {6}
$$

Let us rewrite the Pauli equation in the form

$$
\begin{array}{l} i \hbar \frac {\partial \psi (\boldsymbol {r} , t)}{\partial t} = \left[ \frac {\boldsymbol {p} ^ {2}}{2 M} - \frac {e \hbar A (r , t)}{M c r} l _ {z} \right. \\ \left. + \frac {e ^ {2} A ^ {2} (r , t)}{2 M c ^ {2}} - \lambda \mu B (r, t) \right] \psi (\boldsymbol {r}, t), \tag {7} \\ \end{array}
$$

where $l_{z} = (xp_{y} - yp_{x})/\hbar$ is the projection operator of orbital momentum onto the z axis. Let the momentum of incident electron be P. From the experimental point of view, the momenta $P \gg \hbar k$ and small scattering angles $\theta \ll 1$ are of interest, while the relationship between $q = P\theta/\hbar$ and k can be arbitrary. Under these conditions, the eikonal approximation [11] is valid and we can write the wave function in the form

$$
\psi (\boldsymbol {r}, t) = \exp \left\{- i \frac {P ^ {2} t}{2 M \hbar} + i \frac {P x}{\hbar} \right\} F (\boldsymbol {r}, t). \tag {8}
$$

Then,

$$
i \hbar \frac {\partial F (\boldsymbol {r} , t)}{\partial t} = - i \frac {\hbar P}{M} \frac {\partial F (\boldsymbol {r} , t)}{\partial x} + \frac {e P y A (r , t)}{M c r} F (\boldsymbol {r}, t), \tag {9}
$$

where we have dropped the terms $\partial^{2}F/\partial x^{2}$ , $\partial^{2}F/\partial y^{2}$ and kept only the terms that contain a large momentum P in the numerator. This equation is independent of spin orientation. Using the method of characteristics, we find the solution to Eq. (9),

$$
F (x, y, t) = \exp \left\{- i \frac {e y}{c \hbar} \int_ {- \infty} ^ {x} d z \frac {A (x , y , t + (z - x) / v)}{\sqrt {y ^ {2} + z ^ {2}}} \right\}, \tag {10}
$$

where v = P/M. The shift in the last argument of the function $A(x, y, t)$ corresponds to the retardation effect. Note that the wave function (8), with $F(x,y,t)$ given by (10), satisfies the condition (6).

To obtain the scattering cross section, it is necessary to find the limit

$$
S (y, t ^ {\prime}) = \lim _ {x \to \infty} F (x, y, t)
$$

at a fixed value $t' = t - x / v$ . Replacing in (10) the upper limit of the integral with $\infty$ , we get

$$
S (y, t ^ {\prime}) = e ^ {- i \Xi (y, t ^ {\prime})},
$$

$$
\Xi (y, t ^ {\prime}) = \frac {e y}{c \hbar} \int_ {- \infty} ^ {\infty} \frac {d z}{\sqrt {y ^ {2} + z ^ {2}}}   A (x, y, t ^ {\prime} + z / v)  , \tag {11}
$$

Since the field is time-dependent, the particle flux (cross-section $d\sigma$ ) is also time-dependent. Following the standard derivation, see [11], we obtain

$$
d \sigma (\theta , t) = \frac {P}{2 \pi \hbar} \left| \int_ {- \infty} ^ {+ \infty} d y e ^ {- i q y} \left[ 1 - e ^ {- i \Xi (y, t ^ {\prime})} \right] \right| ^ {2} d \theta , \tag {12}
$$

Making the substitutions of variables $|q|y \to y$ , $|q|z \to z$ and $\omega t' \to \tau$ , we get

$$
d \sigma (\theta , \tau) = \frac {\hbar}{2 \pi P} \left| \int_ {- \infty} ^ {+ \infty} d y e ^ {- i s y} \left[ 1 - e ^ {- i \Xi (y, \tau)} \right] \right| ^ {2} \frac {d \theta}{\theta^ {2}},
$$

$$
\Xi (y, \tau) = \frac {\zeta y Q}{2} \int_ {- \infty} ^ {\infty} \frac {d z}{r} \left[ J _ {1} (\zeta r) \sin (\phi) - N _ {1} (\zeta r) \cos (\phi) \right],
$$

$$
\zeta = \frac {k}{| q |} = \frac {\hbar k}{P \theta}, Q = \frac {e \Phi_ {0}}{2 c \hbar}, \phi = \tau + \frac {c}{v} \zeta z,
$$

$$
r = \sqrt {y ^ {2} + z ^ {2}}, \quad s = \operatorname{sgn} q. \tag {13}
$$

Recall that $\tau = \omega(t - x/v)$ and the cross section in the two-dimensional case has the dimension of length. Let us show that in the stationary case the expression (13) goes over to the known result. To do this, we set $\omega \to 0$ before the change of variables, and obtain the differential scattering cross section for the stationary Aharonov-Bohm effect [1, 11]

$$
d \sigma = \frac {2 \hbar}{\pi P} \sin^ {2} Q \frac {d \theta}{\theta^ {2}}. \tag {14}
$$

For $v/c \ll 1$ and $\zeta \lesssim 1$ the main contribution to the cross section is determined by the integration region $y \ll 1$ and $z \ll 1$ . In this case we find the asymptotics $\Xi_{0}(y, \tau)$ of the function $\Xi(y, \tau)$ ,

$$
\Xi_ {0} (y, \tau) = Q \operatorname{sgn} (y) \cos \tau \exp (- | y | / \vartheta),
$$

$$
\vartheta = \frac {v}{c \zeta} = \frac {\theta}{\theta_ {0}}, \quad \theta_ {0} = \frac {\hbar \omega}{2 E}. \tag {15}
$$

Using integration by parts and the asymptotics (15), we write the expression for the cross section in the form

$$
d \sigma (\theta , \tau) = \sigma_ {0} \left| s G _ {1} (\vartheta , \tau) + \frac {1}{\vartheta} G _ {2} (\vartheta , \tau) \right| ^ {2} d \vartheta ,
$$

$$
G _ {1} (\vartheta , \tau) = \int_ {0} ^ {\infty} d y \sin [ \Xi_ {1} (y, \tau) ] \sin (\vartheta y),
$$

$$
G _ {2} (\vartheta , \tau) = \int_ {0} ^ {\infty} d y \Xi_ {1} (y, \tau) \sin [ \Xi_ {1} (y, \tau) ] \sin (\vartheta y),
$$

$$
\sigma_ {0} = \frac {2 \hbar}{\pi P \theta_ {0}} = \frac {2 v}{\pi \omega}, \quad \Xi_ {1} (y, \tau) = Q e ^ {- y} \cos \tau . \tag {16}
$$

The function $G_{1}(\vartheta, \tau)$ changes sign when replacing $\cos \tau \to -\cos \tau$ , while $G_{2}(\vartheta, \tau)$ does not change sign. For convenience, in Eq. (16) we have passed from the angle $\theta$ to $\vartheta$ .

If $\vartheta \gg 1$ ( $1 \gg \theta \gg \theta_0$ ), then

$$
d \sigma (\vartheta , \tau) = \sigma_ {0} \sin^ {2} (Q \cos \tau) \frac {d \vartheta}{\vartheta^ {2}}. \tag {17}
$$

For $\vartheta \ll 1$ ( $\theta \ll \theta_0$ ), we have

$$
d \sigma (\vartheta , \tau) = \sigma_ {0} \left| \int_ {0} ^ {Q | \cos \tau |} (1 - \cos y) \frac {d y}{y} \right| ^ {2} d \vartheta . \tag {18}
$$

The asymptotics (17) and (18) for the cross sections are independent of $s = \operatorname{sgn}(q)$ , since either the function $G_{1}(\vartheta, \tau)$ contributes to the cross section for $\vartheta \gg 1$ , or $G_{2}(\vartheta, \tau)$ for $\vartheta \ll 1$ . However, for $\vartheta \sim 1$ an asymmetry arises due to the interference of the functions $G_{1}(\vartheta, \tau)$ and $G_{2}(\vartheta, \tau)$ . This asymmetry will be an alternating function of $\tau$ and can be observed by measuring the non-stationary Hall effect (oscillating voltage in the transverse direction).

Let us represent the cross section $d\sigma(\vartheta, \tau)$ in (16) in the form

$$
d \sigma (\vartheta , \tau) = d \sigma_ {s} (\vartheta , \tau) + s d \sigma_ {a} (\vartheta , \tau),
$$

$$
d \sigma_ {s} (\theta , \tau) = \sigma_ {0} \left[ G _ {1} ^ {2} (\vartheta , \tau) + \frac {1}{\vartheta^ {2}} G _ {2} ^ {2} (\vartheta , \tau) \right] d \vartheta ,
$$

$$
d \sigma_ {a} (\theta , \tau) = 2 \sigma_ {0} G _ {1} (\vartheta , \tau) G _ {2} (\vartheta , \tau) \frac {d \vartheta}{\vartheta}. \tag {19}
$$

The functions $G_{1}(\vartheta,\tau)$ and $G_{2}(\vartheta,\tau)$ depend on the parameter Q and variable $\tau$ only through the combination $f = Q\cos\tau$ . Fig. 1 shows the dependence of $\Sigma_{s}(\vartheta,\tau) = \sigma_{0}^{-1}d\sigma_{s}(\vartheta,\tau)/d\vartheta$ and $\Sigma_{a}(\vartheta,\tau) = \sigma_{0}^{-1}d\sigma_{a}(\vartheta,\tau)/d\vartheta$ on $\vartheta$ for several values of f.

It follows from the asymptotics obtained that for alternating current the total scattering cross section $\sigma(\tau)$ is finite, in contrast to the case of direct current. The main contribution to $\sigma(\tau)$ is given by the region of scattering angles $\theta \sim \theta_{0} (\vartheta \sim 1)$ . We have from (12) for $v/c \ll 1$ ,

$$
\sigma (\tau) = \pi \sigma_ {0} \int_ {0} ^ {+ \infty} d y [ 1 - \cos \Xi_ {1} (y, \tau) ]
$$

$$
= \pi \sigma_ {0} \int_ {0} ^ {Q | \cos \tau |} (1 - \cos y) \frac {d y}{y}. \tag {20}
$$

Naturally, there is no asymmetry in $\sigma(\tau)$ . The dependence of $\Sigma_{tot}(\tau)=\sigma(\tau)/\sigma_{0}$ on $\tau$ is shown in Fig.2 for several values of Q.

![](images/1b7ffdcf3a86d09e9a2953e40a0196a8b8d4ea16c8d7eab29b8b93214871675a.jpg)

<details>
<summary>line</summary>

| ϑ   | Σs(ϑ) - Solid Line | Σs(ϑ) - Dashed Line | Σs(ϑ) - Dotted Line |
|-----|--------------------|---------------------|---------------------|
| 0.0 | 0.8                | 2.5                 | 4.5                 |
| 0.5 | 1.2                | 3.0                 | 5.0                 |
| 1.0 | 1.3                | 3.2                 | 4.8                 |
| 1.5 | 1.1                | 2.8                 | 4.2                 |
| 2.0 | 0.8                | 1.5                 | 2.5                 |
| 2.5 | 0.5                | 0.8                 | 1.0                 |
| 3.0 | 0.3                | 0.4                 | 0.5                 |
| 3.5 | 0.2                | 0.2                 | 0.3                 |
| 4.0 | 0.1                | 0.1                 | 0.2                 |
| 4.5 | 0.1                | 0.1                 | 0.1                 |
| 5.0 | 0.1                | 0.1                 | 0.1                 |
| 5.5 | 0.1                | 0.1                 | 0.1                 |
| 6.0 | 0.1                | 0.1                 | 0.1                 |
</details>

![](images/6702d0432117e9c2c1065da445998cb9174570bd2d519804540866ab6cf10e5a.jpg)

<details>
<summary>line</summary>

| ϑ   | Σₐ(ϑ) - Solid | Σₐ(ϑ) - Dashed | Σₐ(ϑ) - Dotted |
|-----|---------------|----------------|----------------|
| 0.0 | 0.0           | 0.0            | 0.0            |
| 0.5 | 1.2           | 3.0            | 4.5            |
| 1.0 | 1.3           | 2.8            | 4.0            |
| 1.5 | 1.1           | 2.2            | 3.5            |
| 2.0 | 0.8           | 1.5            | 2.5            |
| 2.5 | 0.5           | 1.0            | 1.8            |
| 3.0 | 0.3           | 0.6            | 1.2            |
| 3.5 | 0.2           | 0.4            | 0.8            |
| 4.0 | 0.1           | 0.2            | 0.5            |
| 4.5 | 0.05          | 0.1            | 0.3            |
| 5.0 | 0.02          | 0.05           | 0.1            |
| 5.5 | 0.01          | 0.02           | 0.05           |
| 6.0 | 0.0           | 0.0            | 0.0            |
</details>

Figure 1. Dependence of $\Sigma_{s}(\vartheta,\tau)=\sigma_{0}^{-1}d\sigma_{s}(\vartheta,\tau)/d\vartheta$ (top plot) and $\Sigma_{a}(\vartheta,\tau)=\sigma_{0}^{-1}d\sigma_{a}(\vartheta,\tau)/d\vartheta$ (bottom plot) on $\vartheta$ for f=2 (solid curve), f=3 (dashed curve) and f=4 (dotted curve), $f=Q\cos\tau$ .   
![](images/11b6cb6bb6e89aace75756a2cc1fe8d9b6b31902b1ce08266c810ecedfead7f2.jpg)

<details>
<summary>line</summary>

| τ   | Σ_tot(τ) - Solid | Σ_tot(τ) - Dashed | Σ_tot(τ) - Dotted |
| --- | ---------------- | ----------------- | ----------------- |
| 0   | 0.8              | 2.7               | 5.0               |
| 1   | 0.2              | 0.2               | 0.2               |
| 2   | 0.8              | 2.7               | 5.0               |
| 3   | 0.8              | 2.7               | 5.0               |
| 4   | 0.2              | 0.2               | 0.2               |
| 5   | 0.8              | 2.7               | 5.0               |
| 6   | 0.8              | 2.7               | 5.0               |
</details>

Figure 2. Dependence of $\Sigma_{tot}(\tau)=\sigma(\tau)/\sigma_{0}$ on $\tau$ for Q=1 (solid curve), Q=2 (dashed curve) and Q=3 (dotted curve).

The total cross section increases with $Q$ and is well approximated at $Q \gg 1$ by the formula

$$
\sigma (\tau) \approx \pi \sigma_ {0} \ln (1 + 2 Q | \cos \tau |).
$$

The total cross section averaged over time may also be of interest,

$$
\overline {{\sigma}} (Q) = \int_ {0} ^ {2 \pi} \sigma (\tau) \frac {d \tau}{2 \pi} = \pi \sigma_ {0} \int_ {0} ^ {1} [ 1 - J _ {0} (Q y) ] \frac {d y}{y}. \tag {21}
$$

The dependence of $\overline{\Sigma}_{tot}(Q) = \overline{\sigma}(Q) / \sigma_0$ on $Q$ is shown in

Fig. 3. The time-averaged cross section grows logarith-  
![](images/edca25ca30932f8e03b27a90585e5b936b1255ad1c8b8995ae1eeba2bf1f08bc.jpg)

<details>
<summary>line</summary>

| Q  | Σ_tot(Q) |
|----|----------|
| 0  | 0        |
| 5  | 5        |
| 10 | 7        |
| 15 | 8        |
| 20 | 9        |
</details>

Figure 3. Dependence of $\overline{\Sigma}_{tot} = \overline{\sigma}(Q) / \sigma_0$ on $Q$ .

mically with increasing $Q$ for large $Q$ .

# III. CONCLUSION

The non-stationary Aharonov-Bohm effect is investigated in the non-relativistic approximation. Using the eikonal approach, the differential and total scattering cross sections are found. In contrast to the stationary Aharonov-Bohm effect, the total cross section in the non-stationary case is finite. The main contribution to the scattering cross section comes from the angles $\theta \sim \theta_{0} = \hbar\omega/E \ll 1$ . The differential scattering cross section contains an asymmetry with respect to the replacement $q \rightarrow -q$ . This asymmetry has a maximum at $\theta \sim \theta_{0}$ and decreases rapidly at $\theta \gg \theta_{0}$ and $\theta \ll \theta_{0}$ . The asymmetry in the cross section can be observed as the non-stationary Hall effect, and the total scattering cross section manifests itself in oscillations of the total electron flux.

# ACKNOWLEDGEMENT

The work of Ivan Terekhov was financially supported by the ITMO Fellowship Program.

[1] Y. Aharonov and D. Bohm, Significance of electromagnetic potentials in the Quantum theory, Phys. Rev. 115, 485 (1959).   
[2] M. Peshkin and A. Tonomura, The Aharonov-Bohm Effect, Lecture Notes in Physics 340 (Springer-Verlag, Berlin, 1989).   
[3] B. Lee, E. Yin, T. K. Gustafson, R. Chiao, Analysis of Aharonov-Bohm effect due to time-dependent vector potentials, Phys. Rev. A 45, 4319 (1992).   
[4] A. N. Ageev, S. Yu. Davydov, and A. G. Chirkov, Magnetic Aharonov-Bohm effect under time-dependent vector potential, Pis'ma v Zhurnal Tekhnicheskoi Fiziki, 9, 70 (2000) [Technical Physics Letters 26, 392 (2000)].   
[5] B. Gaveau, A. M. Nounou, L. S. Schulman, Homotopy and path integrals in the time-dependent Aharonov-Bohm effect, Found. Phys. 41, 1462 (2011).

[6] D. Singleton and E. C. Vagenas, The covariant, time-dependent Aharonov–Bohm effect, Phys. Lett. B 723, 241 (2013).   
[7] J. Jing, Y.-F. Zhang, K. Wang, Z.-W. Long, S.-H. Dong, On the time-dependent Aharonov–Bohm effect, Phys. Lett. B 774, 87 (2017).   
[8] S. R. Choudhury, S. Mahajan, Direct calculation of time varying Aharonov-Bohm effect, Phys. Lett. A 383(21), 2467 (2019).   
[9] V. I. Ritus, Shift and splitting of atomic energy levels by the field of an electromagnetic wave, JETP 51, 1544 (1967); Sov. Phys. JETP 24(5), 1041 (1967).   
[10] Ya. B. Zel'dovich, Scattering and emission of a quantum system in a strong electromagnetic wave, Sov. Phys. Usp. 16(3), 427 (1973).   
[11] L. D. Landau, E. M. Lifshitz, Quantum Mechanics, non-relativistic theory (Pergamon Press, Oxford, 1977).

<!-- source: data\demo_docs\测试文档.pdf -->

# 测试文档

# 2025年前五个月产品A销售分析

# 一、数据概览

根据图表，产品A在2025年1月至5月的销售额如下：

<table><tr><td>月份</td><td>销售额(万元)</td></tr><tr><td>1月</td><td>100</td></tr><tr><td>2月</td><td>120</td></tr><tr><td>3月</td><td>320</td></tr><tr><td>4月</td><td>240</td></tr><tr><td>5月</td><td>110</td></tr></table>

# 二、趋势分析

整体来看：

- 1月到3月：销售额持续增长，3月达到峰值   
- 3月之后：销售额开始下降  
5月：接近年初水平

说明产品可能在3月存在促销或旺季因素。

# 三、简单计算分析

# 1. 平均销售额

公式:

$$
\bar {x} = \frac {\sum x _ {i}}{n}
$$

计算：

$$
\bar {x} = \frac {1 0 0 + 1 2 0 + 3 2 0 + 2 4 0 + 1 1 0}{5} = 1 7 8
$$

平均销售额为 178万元

# 2. 增长率（以2月→3月为例）

公式:

增长率 $= \frac{\text{本期} - \text{上期}}{\text{上期}}$

计算：

$$
\frac {3 2 0 - 1 2 0}{1 2 0} = 1. 6 7
$$

2月到3月增长显著

# 四、结论

- 3月为销售高峰，应重点分析原因（活动/市场需求）  
- 后续月份下滑明显，需考虑优化策略  
- 建议关注季节性波动并制定对应营销方案

2025年销售额  
![](images/f1d950480a1c25612d5d826d178e70859595889b5ef232b230a0243754736bcd.jpg)

<details>
<summary>line</summary>

| 月份 | 销售额 (万元) |
| ---- | ------------- |
| 1月  | 100           |
| 2月  | 120           |
| 3月  | 320           |
| 4月  | 240           |
| 5月  | 110           |
</details>

<!-- source: data\demo_docs\测试文档2.pdf -->

# 测试文档2

下面是一张二次元美女图片

![](images/bec994ae93999d46627cdc8c061c0776b2b7917bf1a254a84bc64ac62ce7dc45.jpg)

<details>
<summary>natural_image</summary>

Illustration of a girl with purple hair and cat ears, wearing a black dress and white skirt, posing with her hand on her lap (no text or symbols)
</details>